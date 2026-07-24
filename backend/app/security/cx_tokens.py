"""Customer-experience (cx) session tokens.

A deliberately separate, least-privilege identity from the internal
Mission Control ``AuthenticatedUser`` (see ``app.security.token_validator``):
a ``CxTokenService`` mints short-lived tokens scoped to exactly one
``session_id`` + ``workflow_run_id`` pair, so the generated customer
prototype (``app/api/cx.py``) can only ever read/interact with the one
workflow run it was minted for - never any other customer's session, and
never the internal staff APIs.

Tokens are signed HS256 JWTs. The signing key is never hardcoded (see the
Configuration Rules in ``.github/copilot-instructions.md``); it is sourced
from ``Settings.cx_token_signing_key`` (a Key Vault secret in production,
via a Container App Key Vault reference) or, in local/dev mode only, an
ephemeral process-local key generated at startup - never persisted, never
shared across processes, and never usable in production
(``create_cx_token_service`` fails closed there without a configured key).
"""
from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import jwt
from pydantic import BaseModel, ConfigDict, Field

from app.config.settings import Settings

__all__ = [
    "CustomerSessionClaims",
    "CxTokenError",
    "CxTokenInvalidError",
    "CxTokenService",
    "CxTokenServiceError",
    "create_cx_token_service",
]

_ALGORITHM = "HS256"
_SCOPE = "cx_prototype"


class CxTokenError(RuntimeError):
    """Base error for the customer-experience token seam."""


class CxTokenServiceError(CxTokenError):
    """Raised when a ``CxTokenService`` cannot be constructed (fail closed)."""


class CxTokenInvalidError(CxTokenError):
    """Raised when a presented cx token is missing, malformed, expired, or mismatched."""


class CustomerSessionClaims(BaseModel):
    """The verified claims of a validated customer-experience session token."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1)
    workflow_run_id: str = Field(min_length=1)
    expires_at: datetime


class CxTokenService:
    """Mints and validates session-scoped customer-experience access tokens."""

    def __init__(self, *, signing_key: str) -> None:
        if not signing_key.strip():
            raise CxTokenServiceError("CxTokenService requires a non-blank signing key.")
        self._signing_key = signing_key

    def mint(self, *, session_id: str, workflow_run_id: str, ttl_seconds: int) -> str:
        now = datetime.now(UTC)
        payload = {
            "scope": _SCOPE,
            "session_id": session_id,
            "workflow_run_id": workflow_run_id,
            "iat": now,
            "exp": now + timedelta(seconds=ttl_seconds),
        }
        return jwt.encode(payload, self._signing_key, algorithm=_ALGORITHM)

    def validate(self, token: str) -> CustomerSessionClaims:
        try:
            claims = jwt.decode(token, self._signing_key, algorithms=[_ALGORITHM])
        except jwt.PyJWTError as exc:
            raise CxTokenInvalidError(f"Invalid customer-experience token: {exc}") from exc

        if claims.get("scope") != _SCOPE:
            raise CxTokenInvalidError("Customer-experience token has an unexpected scope.")

        session_id = claims.get("session_id")
        workflow_run_id = claims.get("workflow_run_id")
        if not session_id or not workflow_run_id:
            raise CxTokenInvalidError(
                "Customer-experience token is missing required session/workflow_run claims."
            )

        exp = claims.get("exp")
        if not exp:
            raise CxTokenInvalidError("Customer-experience token is missing an 'exp' claim.")

        return CustomerSessionClaims(
            session_id=str(session_id),
            workflow_run_id=str(workflow_run_id),
            expires_at=datetime.fromtimestamp(float(exp), tz=UTC),
        )


def create_cx_token_service(settings: Settings) -> CxTokenService:
    """Build the single ``CxTokenService`` for the current configuration.

    Fails closed (raises ``CxTokenServiceError``) rather than silently
    falling back, mirroring ``create_agent_gateway`` / ``create_token_validator``
    / ``create_speech_to_text_service``:

    - production always requires ``settings.cx_token_signing_key`` to be
      configured (a real Key Vault-backed secret); never substitutes a
      generated key there.
    - local/dev mode uses the configured key if present, otherwise (only
      when ``allow_local_agents`` is True) generates a random, ephemeral,
      process-local key so local development works with zero configuration
      - this key is never persisted and is not valid across process
      restarts or in production.
    """

    if settings.provider_mode == "production":
        if not settings.cx_token_signing_key:
            raise CxTokenServiceError(
                "cx_token_signing_key must be configured in production."
            )
        return CxTokenService(signing_key=settings.cx_token_signing_key)

    if settings.cx_token_signing_key:
        return CxTokenService(signing_key=settings.cx_token_signing_key)

    if settings.allow_local_agents:
        return CxTokenService(signing_key=secrets.token_urlsafe(32))

    raise CxTokenServiceError(
        "No usable cx token signing key: allow_local_agents is False and "
        "cx_token_signing_key is not configured."
    )
