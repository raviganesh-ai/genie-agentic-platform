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

# A dedicated, chat-enabled workflow (not part of _orchestration_helpers, per
# the note in that module about not risking its exact-length assertions):
# two independent steps, each on a different agent, whose prompts declare
# {user_message} explicitly so a customer chat message visibly changes the
# resolved prompt (and, therefore, the local-agent-gateway's deterministic
# output), proving the message actually reached the targeted agent(s).
_CHAT_PROMPTS_YAML = """
prompts:
  - id: chat-prompt-a
    name: Chat Prompt A
    description: Chat-enabled prompt for chat-step-a.
    template: "Process input {x}. Customer message: {user_message}"
    variables:
      - x
      - user_message
  - id: chat-prompt-b
    name: Chat Prompt B
    description: Chat-enabled prompt for chat-step-b.
    template: "Process input {y}. Customer message: {user_message}"
    variables:
      - y
      - user_message
"""

_CHAT_WORKFLOW_YAML = """
workflows:
  - id: chat-workflow
    name: Chat Workflow
    description: Two independent, chat-enabled steps on different agents.
    steps:
      - id: chat-step-a
        agent_id: agent-a
        description: Chat-enabled step for agent-a.
        depends_on: []
        prompt_id: chat-prompt-a
        variable_sources:
          x: transcript
      - id: chat-step-b
        agent_id: agent-b
        description: Chat-enabled step for agent-b.
        depends_on: []
        prompt_id: chat-prompt-b
        variable_sources:
          y: transcript
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


@pytest.fixture
def cx_settings_rate_limited(tmp_path: Path) -> Settings:
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
        cx_reanalysis_rate_limit_per_hour=1,
    )


@pytest.fixture
def chat_cx_settings(tmp_path: Path) -> Settings:
    config_root = tmp_path / "config"
    write_orchestration_config(config_root)
    (config_root / "prompts" / "chat-prompts.yaml").write_text(
        _CHAT_PROMPTS_YAML, encoding="utf-8"
    )
    (config_root / "workflows" / "chat-workflow.yaml").write_text(
        _CHAT_WORKFLOW_YAML, encoding="utf-8"
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


def _create_chat_session_and_run(client: TestClient, headers: dict[str, str]) -> tuple[str, str]:
    session_resp = client.post("/sessions", json={"title": "Chat demo"}, headers=headers)
    assert session_resp.status_code == 201
    session_id = session_resp.json()["id"]

    run_resp = client.post(
        f"/sessions/{session_id}/workflows/chat-workflow/run", json={}, headers=headers
    )
    assert run_resp.status_code == 200
    run_body = run_resp.json()
    assert run_body["status"] == "completed"
    return session_id, run_body["workflow_run_id"]


def _mint_cx_token(
    client: TestClient, *, session_id: str, workflow_run_id: str, headers: dict[str, str]
) -> str:
    mint_resp = client.post(
        f"/sessions/{session_id}/cx-access",
        json={"workflow_run_id": workflow_run_id},
        headers=headers,
    )
    assert mint_resp.status_code == 201
    return mint_resp.json()["path"].split("t=")[1]


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


def test_customer_can_submit_a_reanalysis_request_through_the_minted_link(cx_settings) -> None:
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

        reanalyze_resp = client.post(
            f"/cx/{session_id}/reanalyze",
            params={"t": token},
            json={"request_type": "challenge_recommendation", "rationale": "Too expensive."},
        )
        assert reanalyze_resp.status_code == 201
        body = reanalyze_resp.json()
        assert body["status"] == "routed"
        assert body["routed_to_agent_id"] == "agent-a"


def test_reanalysis_request_is_rejected_without_a_valid_token(cx_settings) -> None:
    app = create_app(settings=cx_settings)
    headers = {"Authorization": f"Bearer {_bearer_token('user-1')}"}

    with TestClient(app) as client:
        session_id, _workflow_run_id = _create_session_and_run(client, headers)

        resp = client.post(
            f"/cx/{session_id}/reanalyze",
            json={"request_type": "challenge_recommendation"},
        )
        assert resp.status_code == 401


def test_reanalysis_request_is_rejected_for_a_token_minted_for_another_session(
    cx_settings,
) -> None:
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

        resp = client.post(
            f"/cx/{session_id_b}/reanalyze",
            params={"t": token_for_a},
            json={"request_type": "challenge_recommendation"},
        )
        assert resp.status_code == 403


def test_reanalysis_requests_are_rate_limited_per_session(cx_settings_rate_limited) -> None:
    app = create_app(settings=cx_settings_rate_limited)
    headers = {"Authorization": f"Bearer {_bearer_token('user-1')}"}

    with TestClient(app) as client:
        session_id, workflow_run_id = _create_session_and_run(client, headers)
        mint_resp = client.post(
            f"/sessions/{session_id}/cx-access",
            json={"workflow_run_id": workflow_run_id},
            headers=headers,
        )
        token = mint_resp.json()["path"].split("t=")[1]

        first = client.post(
            f"/cx/{session_id}/reanalyze",
            params={"t": token},
            json={"request_type": "challenge_recommendation"},
        )
        assert first.status_code == 201

        second = client.post(
            f"/cx/{session_id}/reanalyze",
            params={"t": token},
            json={"request_type": "challenge_recommendation"},
        )
        assert second.status_code == 429


def test_cx_access_mint_reports_zero_dedicated_agents_when_foundry_is_not_configured(
    cx_settings,
) -> None:
    """Local/dev test settings never configure Foundry, so provisioning is a no-op."""

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
        assert mint_resp.json()["dedicated_agents_provisioned"] == 0


def test_cx_access_close_deprovisions_and_is_reported_idempotently(cx_settings) -> None:
    app = create_app(settings=cx_settings)
    headers = {"Authorization": f"Bearer {_bearer_token('user-1')}"}

    with TestClient(app) as client:
        session_id, workflow_run_id = _create_session_and_run(client, headers)
        client.post(
            f"/sessions/{session_id}/cx-access",
            json={"workflow_run_id": workflow_run_id},
            headers=headers,
        )

        close_resp = client.post(f"/sessions/{session_id}/cx-access/close", headers=headers)
        assert close_resp.status_code == 200
        body = close_resp.json()
        assert body["session_id"] == session_id
        # No Foundry configured locally -> nothing was ever provisioned to tear down.
        assert body["dedicated_agents_deprovisioned"] is False


def test_customer_can_chat_with_a_specific_agent_through_the_minted_link(chat_cx_settings) -> None:
    app = create_app(settings=chat_cx_settings)
    headers = {"Authorization": f"Bearer {_bearer_token('user-1')}"}

    with TestClient(app) as client:
        session_id, workflow_run_id = _create_chat_session_and_run(client, headers)
        token = _mint_cx_token(
            client, session_id=session_id, workflow_run_id=workflow_run_id, headers=headers
        )

        chat_resp = client.post(
            f"/cx/{session_id}/chat",
            params={"t": token},
            json={"message": "Can you make this cheaper?", "agent_id": "agent-a"},
        )
        assert chat_resp.status_code == 200
        body = chat_resp.json()
        assert body["status"] == "completed"

        step_a = next(r for r in body["step_results"] if r["step_id"] == "chat-step-a")
        step_b = next(r for r in body["step_results"] if r["step_id"] == "chat-step-b")
        # step-a's resolved prompt now includes the (longer) chat message
        # text, while step-b (not targeted) keeps its original, shorter
        # resolved prompt from the initial run - proving the message
        # reached exactly the one named agent.
        assert "resolved_prompt_length=" in step_a["output_text"]
        original_run_resp = client.get(
            f"/sessions/{session_id}/workflows/runs/{workflow_run_id}", headers=headers
        )
        original_step_b = next(
            r for r in original_run_resp.json()["step_results"] if r["step_id"] == "chat-step-b"
        )
        assert step_b["output_text"] == original_step_b["output_text"]


def test_customer_can_broadcast_a_chat_message_to_every_agent(chat_cx_settings) -> None:
    app = create_app(settings=chat_cx_settings)
    headers = {"Authorization": f"Bearer {_bearer_token('user-1')}"}

    with TestClient(app) as client:
        session_id, workflow_run_id = _create_chat_session_and_run(client, headers)
        token = _mint_cx_token(
            client, session_id=session_id, workflow_run_id=workflow_run_id, headers=headers
        )
        original_run_resp = client.get(
            f"/sessions/{session_id}/workflows/runs/{workflow_run_id}", headers=headers
        )
        original_results = {
            r["step_id"]: r["output_text"] for r in original_run_resp.json()["step_results"]
        }

        chat_resp = client.post(
            f"/cx/{session_id}/chat",
            params={"t": token},
            json={"message": "How does this scale to 10x traffic?"},
        )
        assert chat_resp.status_code == 200
        body = chat_resp.json()
        assert {r["step_id"] for r in body["step_results"]} == {"chat-step-a", "chat-step-b"}
        for result in body["step_results"]:
            assert result["output_text"] != original_results[result["step_id"]]


def test_chat_with_an_unknown_agent_id_returns_404(chat_cx_settings) -> None:
    app = create_app(settings=chat_cx_settings)
    headers = {"Authorization": f"Bearer {_bearer_token('user-1')}"}

    with TestClient(app) as client:
        session_id, workflow_run_id = _create_chat_session_and_run(client, headers)
        token = _mint_cx_token(
            client, session_id=session_id, workflow_run_id=workflow_run_id, headers=headers
        )

        resp = client.post(
            f"/cx/{session_id}/chat",
            params={"t": token},
            json={"message": "Hello?", "agent_id": "no-such-agent"},
        )
        assert resp.status_code == 404


def test_chat_is_rejected_without_a_valid_token(chat_cx_settings) -> None:
    app = create_app(settings=chat_cx_settings)
    headers = {"Authorization": f"Bearer {_bearer_token('user-1')}"}

    with TestClient(app) as client:
        session_id, _workflow_run_id = _create_chat_session_and_run(client, headers)

        resp = client.post(f"/cx/{session_id}/chat", json={"message": "Hello?"})
        assert resp.status_code == 401


def test_progress_reports_every_step_with_agent_names_and_no_output_content(
    chat_cx_settings,
) -> None:
    app = create_app(settings=chat_cx_settings)
    headers = {"Authorization": f"Bearer {_bearer_token('user-1')}"}

    with TestClient(app) as client:
        session_id, workflow_run_id = _create_chat_session_and_run(client, headers)
        token = _mint_cx_token(
            client, session_id=session_id, workflow_run_id=workflow_run_id, headers=headers
        )

        progress_resp = client.get(f"/cx/{session_id}/progress", params={"t": token})
        assert progress_resp.status_code == 200
        body = progress_resp.json()
        assert body["workflow_run_id"] == workflow_run_id
        assert body["status"] == "completed"

        by_step = {step["step_id"]: step for step in body["steps"]}
        assert by_step["chat-step-a"]["agent_id"] == "agent-a"
        assert by_step["chat-step-a"]["agent_name"] == "Agent A"
        assert by_step["chat-step-a"]["status"] == "completed"
        for step in body["steps"]:
            assert "output_text" not in step


def test_progress_is_rejected_for_a_token_minted_for_another_session(chat_cx_settings) -> None:
    app = create_app(settings=chat_cx_settings)
    headers = {"Authorization": f"Bearer {_bearer_token('user-1')}"}

    with TestClient(app) as client:
        session_id_a, run_id_a = _create_chat_session_and_run(client, headers)
        session_id_b, _run_id_b = _create_chat_session_and_run(client, headers)
        token_for_a = _mint_cx_token(
            client, session_id=session_id_a, workflow_run_id=run_id_a, headers=headers
        )

        resp = client.get(f"/cx/{session_id_b}/progress", params={"t": token_for_a})
        assert resp.status_code == 403


def test_customer_can_download_a_starter_kit_zip_of_the_prototype(cx_settings) -> None:
    import io
    import zipfile

    app = create_app(settings=cx_settings)
    headers = {"Authorization": f"Bearer {_bearer_token('user-1')}"}

    with TestClient(app) as client:
        session_id, workflow_run_id = _create_session_and_run(client, headers)
        token = _mint_cx_token(
            client, session_id=session_id, workflow_run_id=workflow_run_id, headers=headers
        )

        resp = client.get(f"/cx/{session_id}/starter-kit", params={"t": token})
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert f"genie-starter-kit-{session_id[:8]}.zip" in resp.headers["content-disposition"]
        assert resp.headers["cache-control"] == "no-store"

        archive = zipfile.ZipFile(io.BytesIO(resp.content))
        names = set(archive.namelist())
        assert names == {"prototype/index.html", "README.md", "ACCESS_POLICY.md"}

        prototype_text = archive.read("prototype/index.html").decode("utf-8")
        assert prototype_text  # the generated prototype output_text, non-empty

        policy_text = archive.read("ACCESS_POLICY.md").decode("utf-8")
        assert str(cx_settings.cx_token_ttl_seconds) in policy_text
        assert str(cx_settings.cx_reanalysis_rate_limit_per_hour) in policy_text
        assert "Dedicated Azure AI Foundry agents provisioned for this session: 0" in policy_text


def test_starter_kit_returns_404_when_the_prototype_has_not_been_generated(
    chat_cx_settings,
) -> None:
    # chat-workflow's steps are not the configured cx_prototype_step_id, so
    # no prototype content has ever been produced for this run.
    app = create_app(settings=chat_cx_settings)
    headers = {"Authorization": f"Bearer {_bearer_token('user-1')}"}

    with TestClient(app) as client:
        session_id, workflow_run_id = _create_chat_session_and_run(client, headers)
        token = _mint_cx_token(
            client, session_id=session_id, workflow_run_id=workflow_run_id, headers=headers
        )

        resp = client.get(f"/cx/{session_id}/starter-kit", params={"t": token})
        assert resp.status_code == 404


def test_starter_kit_is_rejected_without_a_valid_token(cx_settings) -> None:
    app = create_app(settings=cx_settings)
    headers = {"Authorization": f"Bearer {_bearer_token('user-1')}"}

    with TestClient(app) as client:
        session_id, _workflow_run_id = _create_session_and_run(client, headers)

        resp = client.get(f"/cx/{session_id}/starter-kit")
        assert resp.status_code == 401

