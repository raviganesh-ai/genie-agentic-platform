"""Microsoft Entra bearer-token validation with explicit dev-only opt-in."""
from __future__ import annotations

import asyncio
from typing import Any, Protocol

import httpx
import jwt

from app.config.settings import Settings

__all__ = [
    "AuthenticationError",
    "AuthenticationServiceUnavailableError",
    "EntraTokenValidator",
    "LocalDevTokenValidator",
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
    """Raised when Entra discovery/signing-key services are unavailable."""


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


class EntraTokenValidator:
    """Validates Entra access tokens using tenant OpenID metadata and JWKS."""

    def __init__(
        self,
        *,
        authority: str,
        tenant_id: str,
        client_id: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._authority = authority.rstrip("/")
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._http_client = http_client or httpx.AsyncClient(timeout=5.0)
        self._owns_http_client = http_client is None
        self._issuer: str | None = None
        self._keys_by_id: dict[str, Any] = {}
        self._metadata_lock = asyncio.Lock()

    async def _refresh_metadata(self) -> None:
        discovery_url = (
            f"{self._authority}/{self._tenant_id}/v2.0/"
            ".well-known/openid-configuration"
        )
        try:
            discovery_response = await self._http_client.get(discovery_url)
            discovery_response.raise_for_status()
            metadata = discovery_response.json()
            issuer = metadata.get("issuer")
            jwks_uri = metadata.get("jwks_uri")
            if not isinstance(issuer, str) or not isinstance(jwks_uri, str):
                raise TypeError("OpenID metadata omitted issuer or jwks_uri.")
            jwks_response = await self._http_client.get(jwks_uri)
            jwks_response.raise_for_status()
            keys = jwks_response.json().get("keys")
            if not isinstance(keys, list) or not keys:
                raise ValueError("Entra JWKS returned no signing keys.")
            parsed_keys = {
                key["kid"]: jwt.PyJWK.from_dict(key)
                for key in keys
                if isinstance(key, dict) and isinstance(key.get("kid"), str)
            }
            if not parsed_keys:
                raise ValueError("Entra JWKS returned no keyed signing certificates.")
        except (httpx.HTTPError, TypeError, ValueError, KeyError) as exc:
            raise AuthenticationServiceUnavailableError(
                f"Microsoft Entra signing metadata is unavailable: {exc}"
            ) from exc
        self._issuer = issuer
        self._keys_by_id = parsed_keys

    async def validate(
        self,
        token: str,
        *,
        method: str,
        path: str,
    ) -> dict[str, object]:
        del method, path
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthenticationError(f"Malformed bearer token: {exc}") from exc
        key_id = header.get("kid")
        algorithm = header.get("alg")
        if not isinstance(key_id, str) or algorithm != "RS256":
            raise AuthenticationError("Bearer token must use an Entra RS256 signing key.")

        if key_id not in self._keys_by_id:
            async with self._metadata_lock:
                if key_id not in self._keys_by_id:
                    await self._refresh_metadata()
        signing_key = self._keys_by_id.get(key_id)
        if signing_key is None or self._issuer is None:
            raise AuthenticationError("Bearer token references an unknown Entra signing key.")
        try:
            claims = jwt.decode(
                token,
                key=signing_key.key,
                algorithms=["RS256"],
                audience=[self._client_id, f"api://{self._client_id}"],
                issuer=self._issuer,
                options={"require": ["exp", "iat", "iss", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError(f"Invalid Microsoft Entra bearer token: {exc}") from exc
        user_id = claims.get("oid") or claims.get("sub")
        if not user_id or not str(user_id).strip():
            raise AuthenticationError("Bearer token is missing a 'sub' or 'oid' claim.")
        return claims

    async def close(self) -> None:
        if self._owns_http_client:
            await self._http_client.aclose()


class LocalDevTokenValidator:
    """Decodes a token's claims without verifying its signature.

    This is Genie's only token validator (dev/demo deployment, no separate
    production tier). Still requires a syntactically valid JWT with a
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


def create_token_validator(settings: Settings) -> TokenValidator:
    """Select the single ``TokenValidator`` for the current configuration.

    Fails closed (raises ``TokenValidatorError``) rather than silently
    falling back, mirroring ``create_agent_gateway``.
    """

    if settings.entra_authority and settings.entra_tenant_id and settings.entra_client_id:
        return EntraTokenValidator(
            authority=settings.entra_authority,
            tenant_id=settings.entra_tenant_id,
            client_id=settings.entra_client_id,
        )

    if settings.allow_local_token_validation:
        return LocalDevTokenValidator()

    raise TokenValidatorError(
        "No usable token validator: entra_authority, entra_tenant_id, and "
        "entra_client_id are required when local validation is disabled."
    )
