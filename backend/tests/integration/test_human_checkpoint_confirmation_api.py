"""Integration test: the Discovery Wizard's human-in-the-loop confirmation endpoint.

Exercises ``POST /sessions/{id}/peer-review/checkpoints/confirm`` end to end -
the Responsible AI Accountability control that permanently records, as a
real ``GovernanceEvent``, every time a person explicitly proceeds the
Discovery Wizard past a workflow stage. Confirms the recorded event is
attributable to Genie's fixed internal principal (never a client-supplied value)
and is visible via the existing read-only ``GET .../peer-review/events``.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def test_confirm_checkpoint_records_a_governance_event_attributed_to_internal_user(app_local_settings) -> None:
    app = create_app(settings=app_local_settings)

    with TestClient(app) as client:
        session_resp = client.post("/sessions", json={"title": "Discovery run"})
        assert session_resp.status_code == 201
        session_id = session_resp.json()["id"]

        confirm_resp = client.post(
            f"/sessions/{session_id}/peer-review/checkpoints/confirm",
            json={
                "trace_id": "trace-1",
                "stage_key": "requirements",
                "stage_label": "Requirement Discovery",
            },
        )
        assert confirm_resp.status_code == 200
        event = confirm_resp.json()
        assert event["category"] == "human_checkpoint_confirmation"
        assert event["session_id"] == session_id
        assert event["trace_id"] == "trace-1"
        assert event["detail"] == {
            "stage_key": "requirements",
            "stage_label": "Requirement Discovery",
            "confirmed_by": "genie-internal-user",
        }

        events_resp = client.get(f"/sessions/{session_id}/peer-review/events")
        assert events_resp.status_code == 200
        assert any(e["id"] == event["id"] for e in events_resp.json())


def test_confirm_checkpoint_ignores_any_client_supplied_confirmed_by(app_local_settings) -> None:
    """``confirmed_by`` always comes from the internal principal, not the request body."""

    app = create_app(settings=app_local_settings)

    with TestClient(app) as client:
        session_resp = client.post("/sessions", json={"title": "Discovery run"})
        session_id = session_resp.json()["id"]

        confirm_resp = client.post(
            f"/sessions/{session_id}/peer-review/checkpoints/confirm",
            json={
                "trace_id": "trace-1",
                "stage_key": "governance",
                "stage_label": "Governance",
                "confirmed_by": "someone-else",
            },
        )
        assert confirm_resp.status_code == 422  # extra="forbid" rejects the unknown field


def test_confirm_checkpoint_requires_a_valid_session(app_local_settings) -> None:
    app = create_app(settings=app_local_settings)

    with TestClient(app) as client:
        resp = client.post(
            "/sessions/does-not-exist/peer-review/checkpoints/confirm",
            json={"trace_id": "trace-1", "stage_key": "requirements", "stage_label": "Requirement Discovery"},
        )
        assert resp.status_code == 404
