from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def test_discovery_case_is_resumable_listed_and_deletable(app_local_settings) -> None:
    app = create_app(settings=app_local_settings)

    with TestClient(app) as client:
        session_response = client.post("/sessions", json={"title": "Contoso discovery"})
        assert session_response.status_code == 201
        session_id = session_response.json()["id"]

        create_response = client.post(
            f"/sessions/{session_id}/discovery",
            json={"source_upload_ids": []},
        )
        assert create_response.status_code == 201
        created = create_response.json()
        assert created["session_id"] == session_id
        assert created["status"] == "created"
        assert created["version"] == 1

        resumed_response = client.post(
            f"/sessions/{session_id}/discovery",
            json={"source_upload_ids": []},
        )
        assert resumed_response.status_code == 201
        assert resumed_response.json() == created

        get_response = client.get(f"/sessions/{session_id}/discovery")
        assert get_response.status_code == 200
        assert get_response.json() == created

        list_response = client.get("/discovery")
        assert list_response.status_code == 200
        assert list_response.json() == [created]

        delete_response = client.delete(f"/sessions/{session_id}/discovery")
        assert delete_response.status_code == 204
        assert client.get(f"/sessions/{session_id}/discovery").status_code == 404


def test_discovery_case_rejects_unknown_source_upload(app_local_settings) -> None:
    app = create_app(settings=app_local_settings)

    with TestClient(app) as client:
        session_response = client.post("/sessions", json={"title": "Contoso discovery"})
        session_id = session_response.json()["id"]

        response = client.post(
            f"/sessions/{session_id}/discovery",
            json={"source_upload_ids": ["missing-upload"]},
        )

        assert response.status_code == 404
        assert "missing-upload" in response.json()["detail"]