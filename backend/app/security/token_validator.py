"""Bearer token validation: Microsoft Entra ID in production, a local-dev validator otherwise.

Microsoft Identity Service Essentials (MISE) is the only production token
validator. The backend forwards the original bearer token and request context
to the colocated MISE v2 container and fails closed if that service is
unavailable. A deliberately unverified validator remains available only for
local development and tests when local agents are explicitly enabled.
"""
from __future__ import annotations

import base64
import binascii
import json
from typing import Protocol

import httpx

from app.config.settings import Settings

__all__ = [
    "AuthenticationError",
    "AuthenticationServiceUnavailableError",
    "LocalDevTokenValidator",
    "MiseTokenValidator",
    "TokenValidator",
    "TokenValidatorError",
    "create_token_validator",
]


class TokenValidatorError(RuntimeError):
    """Raised when a ``TokenValidator`` cannot be constructed (fail closed)."""


class AuthenticationError(RuntimeError):
    """Raised when a bearer token is missing, malformed, expired, or otherwise invalid."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 401,
        www_authenticate: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.www_authenticate = www_authenticate


class AuthenticationServiceUnavailableError(RuntimeError):
    """Raised when MISE cannot validate a request safely."""


class TokenValidator(Protocol):
    """Validates a raw bearer token string and returns its verified claims."""

    async def validate(
        self,
        token: str,
        *,
        method: str,
        path: str,
    ) -> dict[str, object]: ...

    async def close(self) -> None: ...


class LocalDevTokenValidator:
    """Decodes a token's claims without verifying its signature.

    Never reachable in production (``create_token_validator`` never returns
    this validator when ``provider_mode`` is "production", since
    ``ProductionSafetyValidator`` already requires ``allow_local_agents`` to
    be False there). Still requires a syntactically valid JWT with a
    non-blank ``sub`` (or ``oid``) claim, so an empty/garbage
    ``Authorization`` header is still rejected.
    """

    async def validate(
        self,
        token: str,
        *,
        method: str,
        path: str,
    ) -> dict[str, object]:
        del method, path
        import jwt

        try:
            claims = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError as exc:
            raise AuthenticationError(f"Malformed bearer token: {exc}") from exc

        user_id = claims.get("oid") or claims.get("sub")
        if not user_id or not str(user_id).strip():
            raise AuthenticationError("Bearer token is missing a 'sub' or 'oid' claim.")
        return claims

    async def close(self) -> None:
        return None


class MiseTokenValidator:
    """Validates inbound requests through the MISE v2 container."""

    _subject_claims = ("oid", "sub", "name", "roles")

    def __init__(
        self,
        *,
        endpoint: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=endpoint.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
        )

    async def validate(
        self,
        token: str,
        *,
        method: str,
        path: str,
    ) -> dict[str, object]:
        headers = {
            "Authorization": f"Bearer {token}",
            "Original-Method": method,
            "Original-URI": path,
        }
        for claim in self._subject_claims:
            headers[f"Return-Subject-Token-Claim-{claim}"] = "1"

        try:
            response = await self._client.post(
                "/ValidateRequest",
                headers=headers,
            )
        except httpx.RequestError as exc:
            raise AuthenticationServiceUnavailableError(
                "Authentication service is unavailable."
            ) from exc

        if response.status_code >= 500:
            raise AuthenticationServiceUnavailableError(
                "Authentication service could not validate the request."
            )
        if response.status_code != 200:
            status_code = response.status_code if response.status_code in {401, 403} else 401
            raise AuthenticationError(
                "Bearer token failed validation.",
                status_code=status_code,
                www_authenticate=response.headers.get("www-authenticate"),
            )

        verified_claims: dict[str, object] = {}
        for claim in self._subject_claims:
            value = self._extract_claim(response.headers, claim)
            if value:
                verified_claims[claim] = value

        roles = verified_claims.get("roles")
        if isinstance(roles, str):
            try:
                parsed_roles = json.loads(roles)
            except json.JSONDecodeError:
                parsed_roles = [role.strip() for role in roles.split(",") if role.strip()]
            if isinstance(parsed_roles, list) and all(
                isinstance(role, str) for role in parsed_roles
            ):
                verified_claims["roles"] = parsed_roles

        user_id = verified_claims.get("oid") or verified_claims.get("sub")
        if not user_id or not str(user_id).strip():
            raise AuthenticationError("Validated token is missing a subject claim.")
        return verified_claims

    @staticmethod
    def _extract_claim(headers: httpx.Headers, claim: str) -> str:
        plain = headers.get(f"Subject-Token-Claim-{claim}")
        if plain is not None:
            return plain
        encoded = headers.get(f"Subject-Token-Encoded-Claim-{claim}")
        if encoded is None:
            return ""
        try:
            return base64.b64decode(encoded, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise AuthenticationServiceUnavailableError(
                "Authentication service returned an invalid encoded claim."
            ) from exc

    async def close(self) -> None:
        await self._client.aclose()


def create_token_validator(settings: Settings) -> TokenValidator:
    """Select the single ``TokenValidator`` for the current configuration.

    Fails closed (raises ``TokenValidatorError``) rather than silently
    falling back, mirroring ``create_agent_gateway``.
    """

    entra_configured = bool(settings.entra_tenant_id and settings.entra_client_id)
    mise_configured = bool(settings.mise_endpoint)

    if settings.provider_mode == "production":
        if not entra_configured:
            raise TokenValidatorError(
                "entra_tenant_id and entra_client_id must be configured to authenticate "
                "requests in production."
            )
        if not mise_configured:
            raise TokenValidatorError(
                "mise_endpoint must be configured for production authentication."
            )
        return MiseTokenValidator(
            endpoint=settings.mise_endpoint or "",
            timeout_seconds=settings.mise_timeout_seconds,
        )

    if mise_configured:
        if not entra_configured:
            raise TokenValidatorError(
                "entra_tenant_id and entra_client_id must accompany mise_endpoint."
            )
        return MiseTokenValidator(
            endpoint=settings.mise_endpoint or "",
            timeout_seconds=settings.mise_timeout_seconds,
        )

    if settings.allow_local_token_validation:
        return LocalDevTokenValidator()

    raise TokenValidatorError(
        "No usable token validator: allow_local_token_validation is False and MISE is not "
        "configured."
    )
