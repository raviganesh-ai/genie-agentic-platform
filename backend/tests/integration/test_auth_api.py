"""Integration tests for first-party login and its effect on session ownership.

With ``auth_enabled=False`` (every other integration test's default),
every request resolves to the same internal identity - these tests turn
it on to prove the real, end-to-end behavior the Sev3 incident required:
anonymous access is rejected, a valid login's token is accepted, and two
different logged-in accounts cannot read each other's sessions.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.security.password_hashing import hash_password


@pytest.fixture
def auth_enabled_settings(app_local_settings, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TEST_INTEGRATION_AUTH_SIGNING_KEY", "integration-test-signing-key-0123456789")
    monkeypatch.setenv(
        "TEST_INTEGRATION_AUTH_USERS",
        "\n".join(
            [
                f"alice:{hash_password('alice-password')}",
                f"bob:{hash_password('bob-password')}",
            ]
        ),
    )
    return app_local_settings.model_copy(
        update={
            "auth_enabled": True,
            "auth_token_signing_key_env_var": "TEST_INTEGRATION_AUTH_SIGNING_KEY",
            "auth_users_env_var": "TEST_INTEGRATION_AUTH_USERS",
        }
    )


def _login(client: TestClient, *, username: str, password: str) -> str:
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_auth_status_reports_disabled_by_default(app_local_settings) -> None:
    app = create_app(settings=app_local_settings)
    with TestClient(app) as client:
        response = client.get("/auth/status")
        assert response.status_code == 200
        assert response.json() == {"auth_enabled": False}


def test_auth_status_reports_enabled_when_configured(auth_enabled_settings) -> None:
    app = create_app(settings=auth_enabled_settings)
    with TestClient(app) as client:
        response = client.get("/auth/status")
        assert response.status_code == 200
        assert response.json() == {"auth_enabled": True}


def test_login_rejects_unknown_username(auth_enabled_settings) -> None:
    app = create_app(settings=auth_enabled_settings)
    with TestClient(app) as client:
        response = client.post(
            "/auth/login", json={"username": "nobody", "password": "whatever"}
        )
        assert response.status_code == 401


def test_login_rejects_wrong_password(auth_enabled_settings) -> None:
    app = create_app(settings=auth_enabled_settings)
    with TestClient(app) as client:
        response = client.post(
            "/auth/login", json={"username": "alice", "password": "wrong-password"}
        )
        assert response.status_code == 401


def test_sessions_api_rejects_requests_without_a_token(auth_enabled_settings) -> None:
    app = create_app(settings=auth_enabled_settings)
    with TestClient(app) as client:
        response = client.get("/sessions")
        assert response.status_code == 401


def test_sessions_api_rejects_an_invalid_token(auth_enabled_settings) -> None:
    app = create_app(settings=auth_enabled_settings)
    with TestClient(app) as client:
        response = client.get(
            "/sessions", headers={"Authorization": "Bearer not-a-real-token"}
        )
        assert response.status_code == 401


def test_a_valid_login_can_create_and_read_its_own_session(auth_enabled_settings) -> None:
    app = create_app(settings=auth_enabled_settings)
    with TestClient(app) as client:
        token = _login(client, username="alice", password="alice-password")
        headers = {"Authorization": f"Bearer {token}"}

        created = client.post("/sessions", json={"title": "Alice's mission"}, headers=headers)
        assert created.status_code == 201
        session_id = created.json()["id"]

        fetched = client.get(f"/sessions/{session_id}", headers=headers)
        assert fetched.status_code == 200
        assert fetched.json()["id"] == session_id


def test_one_logged_in_account_cannot_read_another_accounts_session(
    auth_enabled_settings,
) -> None:
    app = create_app(settings=auth_enabled_settings)
    with TestClient(app) as client:
        alice_token = _login(client, username="alice", password="alice-password")
        bob_token = _login(client, username="bob", password="bob-password")

        created = client.post(
            "/sessions",
            json={"title": "Alice's private mission"},
            headers={"Authorization": f"Bearer {alice_token}"},
        )
        assert created.status_code == 201
        session_id = created.json()["id"]

        bob_sees_it = client.get(
            f"/sessions/{session_id}", headers={"Authorization": f"Bearer {bob_token}"}
        )
        assert bob_sees_it.status_code == 403

        bobs_session_list = client.get(
            "/sessions", headers={"Authorization": f"Bearer {bob_token}"}
        )
        assert bobs_session_list.status_code == 200
        assert session_id not in [s["id"] for s in bobs_session_list.json()]
