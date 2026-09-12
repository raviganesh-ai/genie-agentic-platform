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