"""Self-issued, short-lived bearer tokens for first-party platform login.

Genie is both the issuer and the verifier here (unlike
``app.iq.delegated_token_broker``, which validates Microsoft-issued ID
tokens via JWKS) - a single HMAC-SHA256 (HS256) signing key is enough,
with no external identity provider involved. See
``Settings.auth_token_signing_key_env_var``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

__all__ = ["TokenClaims", "TokenValidationError", "issue_token", "validate_token"]

_ALGORITHM = "HS256"


class TokenValidationError(RuntimeError):
    """Raised for any missing, malformed, expired, or mis-signed token.

    Deliberately a single exception type (not PyJWT's own hierarchy) so
    callers (the ``get_current_user`` dependency) depend on this module's
    own interface rather than the JWT library's - see "isolate assumptions
    behind interfaces" in the repository's coding instructions.
    """


@dataclass(frozen=True)
class TokenClaims:
    """The identity and authorization claims carried by a validated token."""

    user_id: str
    display_name: str
    roles: tuple[str, ...]


def issue_token(
    *,
    user_id: str,
    display_name: str,
    roles: tuple[str, ...],
    signing_key: str,
    ttl_seconds: float,
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "name": display_name,
        "roles": list(roles),
        "iat": now,
        "exp": now + timedelta(seconds=ttl_seconds),
    }
    return jwt.encode(payload, signing_key, algorithm=_ALGORITHM)


def validate_token(token: str, *, signing_key: str) -> TokenClaims:
    try:
        payload = jwt.decode(
            token,
            key=signing_key,
            algorithms=[_ALGORITHM],
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenValidationError(f"The bearer token could not be validated: {exc}") from exc
    user_id = payload.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise TokenValidationError("The bearer token has no stable subject claim.")
    display_name = payload.get("name")
    roles = payload.get("roles")
    return TokenClaims(
        user_id=user_id,
        display_name=display_name if isinstance(display_name, str) else "",
        roles=tuple(roles) if isinstance(roles, list) else (),
    )
