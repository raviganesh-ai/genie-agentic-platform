"""Tests for Genie's request identity dependency.

Covers both states: first-party authentication disabled (every existing
dev/test environment's default - the single deterministic internal
identity, unchanged from before) and enabled (real bearer-token
validation - missing/invalid/expired tokens are rejected with 401, a
valid token's claims become the returned identity).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.security.auth_models import GENIE_ADMIN_ROLE
from app.security.auth_service import AuthService
from app.security.dependencies import get_current_user
from app.security.password_hashing import hash_password
from app.security.token_service import issue_token


class _FakeRequest:
    """Stands in for ``fastapi.Request``, exposing only what the dependency reads."""

    def __init__(self, *, auth_service: AuthService, headers: dict[str, str] | None = None) -> None:
        self.app = SimpleNamespace(state=SimpleNamespace(auth_service=auth_service))
        self.headers = headers or {}


def _disabled_auth_service() -> AuthService:
    return AuthService(
        enabled=False, signing_key_env_var=None, users_env_var=None, token_ttl_seconds=3600
    )


def _enabled_auth_service(monkeypatch: pytest.MonkeyPatch, *, username: str = "alice") -> AuthService:
    monkeypatch.setenv("TEST_AUTH_SIGNING_KEY", "unit-test-signing-key")
    monkeypatch.setenv("TEST_AUTH_USERS", f"{username}:{hash_password('correct-horse')}")
    return AuthService(
        enabled=True,
        signing_key_env_var="TEST_AUTH_SIGNING_KEY",
        users_env_var="TEST_AUTH_USERS",
        token_ttl_seconds=3600,
    )


async def test_current_user_is_fixed_internal_admin_when_auth_disabled() -> None:
    request = _FakeRequest(auth_service=_disabled_auth_service())

    first = await get_current_user(request)  # type: ignore[arg-type]
    second = await get_current_user(request)  # type: ignore[arg-type]

    assert first.user_id == "genie-internal-user"
    assert first.object_id == "genie-internal-user"
    assert first.display_name == "Genie Internal User"
    assert first.roles == [GENIE_ADMIN_ROLE]
    assert first is not second

    first.roles.clear()
    assert second.roles == [GENIE_ADMIN_ROLE]


async def test_missing_authorization_header_is_rejected_when_auth_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _FakeRequest(auth_service=_enabled_auth_service(monkeypatch))

    with pytest.raises(HTTPException) as excinfo:
        await get_current_user(request)  # type: ignore[arg-type]

    assert excinfo.value.status_code == 401


async def test_invalid_bearer_token_is_rejected_when_auth_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _FakeRequest(
        auth_service=_enabled_auth_service(monkeypatch),
        headers={"Authorization": "Bearer not-a-real-token"},
    )

    with pytest.raises(HTTPException) as excinfo:
        await get_current_user(request)  # type: ignore[arg-type]

    assert excinfo.value.status_code == 401


async def test_valid_bearer_token_resolves_to_its_own_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth_service = _enabled_auth_service(monkeypatch, username="alice")
    token = issue_token(
        user_id="alice",
        display_name="alice",
        roles=(GENIE_ADMIN_ROLE,),
        signing_key="unit-test-signing-key",
        ttl_seconds=3600,
    )
    request = _FakeRequest(auth_service=auth_service, headers={"Authorization": f"Bearer {token}"})

    user = await get_current_user(request)  # type: ignore[arg-type]

    assert user.user_id == "alice"
    assert user.roles == [GENIE_ADMIN_ROLE]


async def test_expired_bearer_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    auth_service = _enabled_auth_service(monkeypatch, username="alice")
    expired_token = issue_token(
        user_id="alice",
        display_name="alice",
        roles=(GENIE_ADMIN_ROLE,),
        signing_key="unit-test-signing-key",
        ttl_seconds=-1,
    )
    request = _FakeRequest(
        auth_service=auth_service, headers={"Authorization": f"Bearer {expired_token}"}
    )

    with pytest.raises(HTTPException) as excinfo:
        await get_current_user(request)  # type: ignore[arg-type]

    assert excinfo.value.status_code == 401
