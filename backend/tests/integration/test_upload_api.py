from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def test_upload_api_returns_metadata_without_internal_transcript(app_local_settings) -> None:
    app = create_app(settings=app_local_settings)

    with TestClient(app) as client:
        session_response = client.post("/sessions", json={"title": "Large evidence upload"})
        assert session_response.status_code == 201
        session_id = session_response.json()["id"]

        upload_response = client.post(
            f"/sessions/{session_id}/uploads/transcript",
            files={"file": ("customer.txt", b"Customer evidence", "text/plain")},
        )

        assert upload_response.status_code == 201
        assert upload_response.json()["file_name"] == "customer.txt"
        assert "transcript_text" not in upload_response.json()

        list_response = client.get(f"/sessions/{session_id}/uploads")
        assert list_response.status_code == 200
        assert len(list_response.json()) == 1
        assert "transcript_text" not in list_response.json()[0]


def test_delete_upload_removes_record_and_discovery_reference(app_local_settings) -> None:
    app = create_app(settings=app_local_settings)

    with TestClient(app) as client:
        session_id = client.post("/sessions", json={"title": "Removable evidence"}).json()["id"]
        upload_id = client.post(
            f"/sessions/{session_id}/uploads/transcript",
            files={"file": ("customer.txt", b"Customer evidence", "text/plain")},
        ).json()["id"]
        create_response = client.post(
            f"/sessions/{session_id}/discovery",
            json={"source_upload_ids": [upload_id]},
        )
        assert create_response.status_code == 201

        delete_response = client.delete(f"/sessions/{session_id}/uploads/{upload_id}")

        assert delete_response.status_code == 204
        assert client.get(f"/sessions/{session_id}/uploads").json() == []
        assert client.get(f"/sessions/{session_id}/uploads/{upload_id}").status_code == 404
        discovery = client.get(f"/sessions/{session_id}/discovery").json()
        assert discovery["source_upload_ids"] == []