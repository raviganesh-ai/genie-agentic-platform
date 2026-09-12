from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.dependencies import get_document_understanding_service
from app.main import create_app
from app.services.document_understanding_service import DocumentUnderstandingService


class FakeDocumentUnderstandingService(DocumentUnderstandingService):
    async def validate_ready(self) -> None:
        return None

    async def extract(self, *, content: bytes, content_type: str, file_name: str) -> str:
        assert content == b"image bytes"
        assert content_type == "image/png"
        assert file_name == "current-state.png"
        return "# Current state\n\nThe diagram shows an unsupported manual approval step."


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


def test_upload_api_uses_document_understanding_for_image_evidence(app_local_settings) -> None:
    app = create_app(settings=app_local_settings)
    app.dependency_overrides[get_document_understanding_service] = (
        lambda: FakeDocumentUnderstandingService()
    )

    with TestClient(app) as client:
        session_id = client.post("/sessions", json={"title": "Visual evidence"}).json()["id"]
        upload_response = client.post(
            f"/sessions/{session_id}/uploads/supporting_document",
            files={"file": ("current-state.png", b"image bytes", "image/png")},
        )

        assert upload_response.status_code == 201
        assert upload_response.json()["status"] == "completed"
        upload_id = upload_response.json()["id"]
        persisted = app.state.session_service._upload_repository._records[upload_id]
        assert "unsupported manual approval step" in persisted.transcript_text


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