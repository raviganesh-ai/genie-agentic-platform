"""Orchestrates first-party login: validates credentials, issues tokens.

Constructed once at startup from ``Settings`` (see ``app.main``) and reused
across every request via ``app.api.dependencies.get_auth_service`` - mirrors
every other Phase 7 service's "build once, inject per request" wiring.
Resolves its two secret environment variables lazily, at call time, the
same way ``app.iq.delegated_token_broker`` resolves
``client_secret_env_var`` - so rotating either secret only requires setting
the new environment variable value, never a code change.
"""
from __future__ import annotations

import os

from app.security.auth_models import GENIE_ADMIN_ROLE
from app.security.password_hashing import parse_user_records, verify_password
from app.security.token_service import TokenClaims, issue_token, validate_token

__all__ = ["AuthenticationError", "AuthService", "AuthNotConfiguredError"]


class AuthenticationError(RuntimeError):
    """Raised when login credentials do not match a configured account."""


class AuthNotConfiguredError(RuntimeError):
    """Raised when login/token validation is attempted but auth is disabled
    or missing its required secret configuration."""


class AuthService:
    def __init__(
        self,
        *,
        enabled: bool,
        signing_key_env_var: str | None,
        users_env_var: str | None,
        token_ttl_seconds: float,
    ) -> None:
        self._enabled = enabled
        self._signing_key_env_var = signing_key_env_var
        self._users_env_var = users_env_var
        self._token_ttl_seconds = token_ttl_seconds

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def token_ttl_seconds(self) -> float:
        return self._token_ttl_seconds

    def _signing_key(self) -> str:
        if not self._signing_key_env_var:
            raise AuthNotConfiguredError("No auth token signing key is configured.")
        key = os.environ.get(self._signing_key_env_var)
        if not key:
            raise AuthNotConfiguredError(
                f"Environment variable '{self._signing_key_env_var}' is not set."
            )
        return key

    def _users(self) -> dict[str, str]:
        if not self._users_env_var:
            raise AuthNotConfiguredError("No auth user records are configured.")
        raw = os.environ.get(self._users_env_var)
        if not raw:
            raise AuthNotConfiguredError(
                f"Environment variable '{self._users_env_var}' is not set."
            )
        return parse_user_records(raw)

    def authenticate(self, *, username: str, password: str) -> str:
        """Validate credentials and return an issued bearer token.

        Raises :class:`AuthNotConfiguredError` if auth is disabled/misconfigured,
        or :class:`AuthenticationError` if the username/password do not match.
        """

        if not self._enabled:
            raise AuthNotConfiguredError("First-party authentication is not enabled.")
        encoded_hash = self._users().get(username)
        if encoded_hash is None or not verify_password(password, encoded_hash):
            raise AuthenticationError("Invalid username or password.")
        return issue_token(
            user_id=username,
            display_name=username,
            roles=(GENIE_ADMIN_ROLE,),
            signing_key=self._signing_key(),
            ttl_seconds=self._token_ttl_seconds,
        )

    def validate_bearer_token(self, token: str) -> TokenClaims:
        """Validate a bearer token and return its claims.

        Raises :class:`AuthNotConfiguredError` if auth is disabled/misconfigured,
        or :class:`TokenValidationError` if the token itself is invalid.
        """

        if not self._enabled:
            raise AuthNotConfiguredError("First-party authentication is not enabled.")
        return validate_token(token, signing_key=self._signing_key())
