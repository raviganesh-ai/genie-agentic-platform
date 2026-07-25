"""Integration test: the Discovery Wizard's human-in-the-loop confirmation endpoint.

Exercises ``POST /sessions/{id}/governance/checkpoints/confirm`` end to end -
the Responsible AI Accountability control that permanently records, as a
real ``GovernanceEvent``, every time a person explicitly proceeds the
Discovery Wizard past a workflow stage. Confirms the recorded event is
attributable to the authenticated caller (never a client-supplied value)
and is visible via the existing read-only ``GET .../governance/events``.
"""
from __future__ import annotations

import jwt
from fastapi.testclient import TestClient

from app.main import create_app


def _bearer_token(user_id: str) -> str:
    return jwt.encode({"sub": user_id}, "unit-test-secret", algorithm="HS256")


def test_confirm_checkpoint_records_a_governance_event_attributed_to_the_caller(local_settings) -> None:
    app = create_app(settings=local_settings)
    headers = {"Authorization": f"Bearer {_bearer_token('user-1')}"}

    with TestClient(app) as client:
        session_resp = client.post("/sessions", json={"title": "Discovery run"}, headers=headers)
        assert session_resp.status_code == 201
        session_id = session_resp.json()["id"]

        confirm_resp = client.post(
            f"/sessions/{session_id}/governance/checkpoints/confirm",
            json={
                "trace_id": "trace-1",
                "stage_key": "requirements",
                "stage_label": "Requirement Discovery",
            },
            headers=headers,
        )
        assert confirm_resp.status_code == 200
        event = confirm_resp.json()
        assert event["category"] == "human_checkpoint_confirmation"
        assert event["session_id"] == session_id
        assert event["trace_id"] == "trace-1"
        assert event["detail"] == {
            "stage_key": "requirements",
            "stage_label": "Requirement Discovery",
            "confirmed_by": "user-1",
        }

        events_resp = client.get(f"/sessions/{session_id}/governance/events", headers=headers)
        assert events_resp.status_code == 200
        assert any(e["id"] == event["id"] for e in events_resp.json())


def test_confirm_checkpoint_ignores_any_client_supplied_confirmed_by(local_settings) -> None:
    """``confirmed_by`` always comes from the authenticated user - the request body has no such field."""

    app = create_app(settings=local_settings)
    headers = {"Authorization": f"Bearer {_bearer_token('user-2')}"}

    with TestClient(app) as client:
        session_resp = client.post("/sessions", json={"title": "Discovery run"}, headers=headers)
        session_id = session_resp.json()["id"]

        confirm_resp = client.post(
            f"/sessions/{session_id}/governance/checkpoints/confirm",
            json={
                "trace_id": "trace-1",
                "stage_key": "governance",
                "stage_label": "Governance",
                "confirmed_by": "someone-else",
            },
            headers=headers,
        )
        assert confirm_resp.status_code == 422  # extra="forbid" rejects the unknown field


def test_confirm_checkpoint_requires_a_valid_session(local_settings) -> None:
    app = create_app(settings=local_settings)
    headers = {"Authorization": f"Bearer {_bearer_token('user-1')}"}

    with TestClient(app) as client:
        resp = client.post(
            "/sessions/does-not-exist/governance/checkpoints/confirm",
            json={"trace_id": "trace-1", "stage_key": "requirements", "stage_label": "Requirement Discovery"},
            headers=headers,
        )
        assert resp.status_code == 404
