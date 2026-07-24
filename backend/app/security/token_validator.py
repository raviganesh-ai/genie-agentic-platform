"""Bearer token validation: Microsoft Entra ID in production, a local-dev validator otherwise.

Mirrors the ``AgentGateway`` / ``create_agent_gateway`` seam established in
Phase 3: production must validate every token's signature, issuer, and
audience against Entra ID; local/dev may use an unverified validator only
when Entra ID is not configured AND ``Settings.allow_local_agents`` is True
(the same flag ``ProductionSafetyValidator`` already forces False in
production, so this seam is automatically fail-closed with no new
configuration surface). If Entra ID *is* configured, its validator is used
regardless of ``provider_mode``, so a developer can exercise the real
integration locally too.

Never invents undocumented Entra ID/Azure AD behavior beyond what PyJWT's
documented JWKS client and the standard Microsoft identity platform
discovery/JWKS endpoints provide; anything uncertain is isolated behind the
``TokenValidator`` protocol so it can be swapped without touching callers.
"""
from __future__ import annotations

from typing import Protocol

from app.config.settings import Settings

__all__ = [
    "AuthenticationError",
    "EntraIdTokenValidator",
    "LocalDevTokenValidator",
    "TokenValidator",
    "TokenValidatorError",
    "create_token_validator",
]


class TokenValidatorError(RuntimeError):
    """Raised when a ``TokenValidator`` cannot be constructed (fail closed)."""


class AuthenticationError(RuntimeError):
    """Raised when a bearer token is missing, malformed, expired, or otherwise invalid."""


class TokenValidator(Protocol):
    """Validates a raw bearer token string and returns its verified claims."""

    def validate(self, token: str) -> dict[str, object]: ...


class LocalDevTokenValidator:
    """Decodes a token's claims without verifying its signature.

    Never reachable in production (``create_token_validator`` never returns
    this validator when ``provider_mode`` is "production", since
    ``ProductionSafetyValidator`` already requires ``allow_local_agents`` to
    be False there). Still requires a syntactically valid JWT with a
    non-blank ``sub`` (or ``oid``) claim, so an empty/garbage
    ``Authorization`` header is still rejected.
    """

    def validate(self, token: str) -> dict[str, object]:
        import jwt

        try:
            claims = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError as exc:
            raise AuthenticationError(f"Malformed bearer token: {exc}") from exc

        user_id = claims.get("oid") or claims.get("sub")
        if not user_id or not str(user_id).strip():
            raise AuthenticationError("Bearer token is missing a 'sub' or 'oid' claim.")
        return claims


class EntraIdTokenValidator:
    """Validates a token's signature, issuer, and audience against Microsoft Entra ID.

    Uses PyJWT's ``PyJWKClient`` against the tenant's documented JWKS
    endpoint (``https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys``)
    to verify the RS256 signature, and requires the token's audience to
    match the configured client id and its issuer to match either the v1
    or v2 Microsoft identity platform issuer for this tenant.
    """

    def __init__(self, *, tenant_id: str, client_id: str) -> None:
        import jwt

        self._tenant_id = tenant_id
        self._client_id = client_id
        self._jwk_client = jwt.PyJWKClient(
            f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
        )
        self._valid_issuers = {
            f"https://login.microsoftonline.com/{tenant_id}/v2.0",
            f"https://sts.windows.net/{tenant_id}/",
        }

    def validate(self, token: str) -> dict[str, object]:
        import jwt

        try:
            signing_key = self._jwk_client.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                audience=self._client_id,
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError(f"Bearer token failed validation: {exc}") from exc

        if claims.get("iss") not in self._valid_issuers:
            raise AuthenticationError(
                f"Bearer token issuer '{claims.get('iss')}' is not a trusted issuer for "
                f"tenant '{self._tenant_id}'."
            )
        return claims


def create_token_validator(settings: Settings) -> TokenValidator:
    """Select the single ``TokenValidator`` for the current configuration.

    Fails closed (raises ``TokenValidatorError``) rather than silently
    falling back, mirroring ``create_agent_gateway``.
    """

    entra_configured = bool(settings.entra_tenant_id and settings.entra_client_id)

    if settings.provider_mode == "production":
        if not entra_configured:
            raise TokenValidatorError(
                "entra_tenant_id and entra_client_id must be configured to authenticate "
                "requests in production."
            )
        return EntraIdTokenValidator(
            tenant_id=settings.entra_tenant_id or "", client_id=settings.entra_client_id or ""
        )

    if entra_configured:
        return EntraIdTokenValidator(
            tenant_id=settings.entra_tenant_id or "", client_id=settings.entra_client_id or ""
        )

    if settings.allow_local_agents:
        return LocalDevTokenValidator()

    raise TokenValidatorError(
        "No usable token validator: allow_local_agents is False and Microsoft Entra ID "
        "is not configured."
    )
