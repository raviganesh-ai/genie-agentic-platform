"""Bearer token validation for Genie's dev/demo backend.

Genie is a personal dev/demo deployment with no separate production tier.
The only token validator is a local-dev validator that decodes a bearer
token's claims without verifying its signature - still requires a
syntactically valid JWT with a non-blank subject claim, so an empty or
garbage ``Authorization`` header is rejected.
"""
from __future__ import annotations

from typing import Protocol

from app.config.settings import Settings

__all__ = [
    "AuthenticationError",
    "AuthenticationServiceUnavailableError",
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
    """Reserved for a future networked token validator that can be unreachable.

    Not raised by ``LocalDevTokenValidator`` (kept for API stability - see
    ``app.security.dependencies.get_current_user``'s except clause).
    """


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

    if settings.allow_local_token_validation:
        return LocalDevTokenValidator()

    raise TokenValidatorError(
        "No usable token validator: allow_local_token_validation is False."
    )
