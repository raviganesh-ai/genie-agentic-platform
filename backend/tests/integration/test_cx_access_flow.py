"""Integration test: the customer-experience (cx) secure access flow end to end.

Exercises the full path introduced for the secure, least-privilege
customer-facing prototype surface: an internal user mints a session-scoped
link (``POST /sessions/{id}/cx-access``), the generated prototype is served
only to a valid token for that exact session (``GET /cx/{id}/app``), the
token is re-usable via the ``HttpOnly`` cookie set on first load, and every
cross-session or expired-token access attempt is rejected.
"""
from __future__ import annotations

import time
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.main import create_app

from ._orchestration_helpers import write_orchestration_config

_PROTOTYPE_WORKFLOW_YAML = """
workflows:
  - id: prototype-workflow
    name: Prototype Workflow
    description: A single step whose output is the customer-facing prototype.
    steps:
      - id: generate-prototype
        agent_id: agent-a
        description: Produces the sample prototype content.
        depends_on: []
        prompt_id: prompt-a
    enabled: true
"""


def _bearer_token(user_id: str) -> str:
    return jwt.encode({"sub": user_id}, "unit-test-secret", algorithm="HS256")


@pytest.fixture
def cx_settings(tmp_path: Path) -> Settings:
    config_root = tmp_path / "config"
    write_orchestration_config(config_root)
    (config_root / "workflows" / "prototype-workflow.yaml").write_text(
        _PROTOTYPE_WORKFLOW_YAML, encoding="utf-8"
    )
    return Settings(
        environment="development",
        provider_mode="local",
        governance_provider="local",
        allow_mock_agents=True,
        allow_local_agents=True,
        use_synthetic_data=True,
        config_root=config_root,
        cx_token_ttl_seconds=60,
    )


def _create_session_and_run(client: TestClient, headers: dict[str, str]) -> tuple[str, str]:
    session_resp = client.post("/sessions", json={"title": "Prototype demo"}, headers=headers)
    assert session_resp.status_code == 201
    session_id = session_resp.json()["id"]

    run_resp = client.post(
        f"/sessions/{session_id}/workflows/prototype-workflow/run",
        json={
            "step_inputs": {
                "generate-prototype": {
                    "step_id": "generate-prototype",
                    "variables": {"x": "sample solution architecture"},
                }
            }
        },
        headers=headers,
    )
    assert run_resp.status_code == 200
    run_body = run_resp.json()
    assert run_body["status"] == "completed"
    return session_id, run_body["workflow_run_id"]


def test_minted_link_serves_the_prototype_and_sets_a_session_scoped_cookie(cx_settings) -> None:
    app = create_app(settings=cx_settings)
    headers = {"Authorization": f"Bearer {_bearer_token('user-1')}"}

    with TestClient(app) as client:
        session_id, workflow_run_id = _create_session_and_run(client, headers)

        mint_resp = client.post(
            f"/sessions/{session_id}/cx-access",
            json={"workflow_run_id": workflow_run_id},
            headers=headers,
        )
        assert mint_resp.status_code == 201
        link = mint_resp.json()
        assert link["path"].startswith(f"/cx/{session_id}/app?t=")

        app_resp = client.get(link["path"])
        assert app_resp.status_code == 200
        assert "local-agent-gateway" in app_resp.text
        assert app_resp.headers["content-security-policy"].startswith("default-src 'none'")
        assert app_resp.headers["cache-control"] == "no-store"
        set_cookie = app_resp.headers.get("set-cookie", "")
        assert "genie_cx_session=" in set_cookie
        assert "HttpOnly" in set_cookie
        assert f"Path=/cx/{session_id}" in set_cookie


def test_cookie_alone_grants_repeat_access_without_the_query_token(cx_settings) -> None:
    app = create_app(settings=cx_settings)
    headers = {"Authorization": f"Bearer {_bearer_token('user-1')}"}

    with TestClient(app) as client:
        session_id, workflow_run_id = _create_session_and_run(client, headers)
        mint_resp = client.post(
            f"/sessions/{session_id}/cx-access",
            json={"workflow_run_id": workflow_run_id},
            headers=headers,
        )
        token = mint_resp.json()["path"].split("t=")[1]

        first = client.get(f"/cx/{session_id}/app", params={"t": token})
        assert first.status_code == 200

        repeat = client.get(f"/cx/{session_id}/app", cookies={"genie_cx_session": token})
        assert repeat.status_code == 200
        assert "local-agent-gateway" in repeat.text


def test_token_minted_for_one_session_is_rejected_for_another_session(cx_settings) -> None:
    app = create_app(settings=cx_settings)
    headers = {"Authorization": f"Bearer {_bearer_token('user-1')}"}

    with TestClient(app) as client:
        session_id_a, run_id_a = _create_session_and_run(client, headers)
        session_id_b, _run_id_b = _create_session_and_run(client, headers)

        mint_resp = client.post(
            f"/sessions/{session_id_a}/cx-access",
            json={"workflow_run_id": run_id_a},
            headers=headers,
        )
        token_for_a = mint_resp.json()["path"].split("t=")[1]

        cross_resp = client.get(f"/cx/{session_id_b}/app", params={"t": token_for_a})
        assert cross_resp.status_code == 403


def test_expired_token_is_rejected(cx_settings) -> None:
    app = create_app(settings=cx_settings)
    headers = {"Authorization": f"Bearer {_bearer_token('user-1')}"}

    with TestClient(app) as client:
        session_id, workflow_run_id = _create_session_and_run(client, headers)

        expired_token = app.state.cx_token_service.mint(
            session_id=session_id, workflow_run_id=workflow_run_id, ttl_seconds=0
        )
        time.sleep(1.1)

        resp = client.get(f"/cx/{session_id}/app", params={"t": expired_token})
        assert resp.status_code == 401


def test_status_endpoint_reports_completion_without_exposing_step_content(cx_settings) -> None:
    app = create_app(settings=cx_settings)
    headers = {"Authorization": f"Bearer {_bearer_token('user-1')}"}

    with TestClient(app) as client:
        session_id, workflow_run_id = _create_session_and_run(client, headers)
        mint_resp = client.post(
            f"/sessions/{session_id}/cx-access",
            json={"workflow_run_id": workflow_run_id},
            headers=headers,
        )
        token = mint_resp.json()["path"].split("t=")[1]

        status_resp = client.get(f"/cx/{session_id}/status", params={"t": token})
        assert status_resp.status_code == 200
        body = status_resp.json()
        assert body == {
            "workflow_run_id": workflow_run_id,
            "status": "completed",
            "steps_completed": 1,
            "steps_total": 1,
        }


def test_missing_token_is_rejected(cx_settings) -> None:
    app = create_app(settings=cx_settings)
    headers = {"Authorization": f"Bearer {_bearer_token('user-1')}"}

    with TestClient(app) as client:
        session_id, _workflow_run_id = _create_session_and_run(client, headers)

        resp = client.get(f"/cx/{session_id}/app")
        assert resp.status_code == 401


def test_cx_access_link_cannot_be_minted_for_a_workflow_run_from_another_session(
    cx_settings,
) -> None:
    app = create_app(settings=cx_settings)
    headers = {"Authorization": f"Bearer {_bearer_token('user-1')}"}

    with TestClient(app) as client:
        _session_id_a, run_id_a = _create_session_and_run(client, headers)
        session_id_b, _run_id_b = _create_session_and_run(client, headers)

        mint_resp = client.post(
            f"/sessions/{session_id_b}/cx-access",
            json={"workflow_run_id": run_id_a},
            headers=headers,
        )
        assert mint_resp.status_code == 404
