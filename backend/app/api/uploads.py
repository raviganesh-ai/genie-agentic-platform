"""Upload API routes: transcript/audio/video/supporting-document uploads.

Thin wrapper over ``SessionService.register_upload`` / ``list_uploads`` /
``get_upload``. Actual file bytes are accepted via ``UploadFile`` but never
persisted to disk/blob storage here - wiring a durable, Azure Storage-backed
upload sink is later-phase (``app.transcription``/ingestion pipeline) work;
this router only records the upload's metadata and starting ingestion
status, per Phase 7's API-layer scope.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, UploadFile

from app.api.dependencies import get_session_service
from app.models.upload_models import UploadRecord, UploadType
from app.security.auth_models import AuthenticatedUser
from app.security.dependencies import get_current_user
from app.services.session_service import SessionService

router = APIRouter(prefix="/sessions/{session_id}/uploads", tags=["uploads"])


@router.post("/{upload_type}", status_code=201)
async def create_upload(
    session_id: str,
    upload_type: UploadType,
    file: UploadFile,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
) -> UploadRecord:
    contents = await file.read()
    return await session_service.register_upload(
        session_id=session_id,
        requesting_user_id=user.user_id,
        upload_type=upload_type,
        file_name=file.filename or "unnamed",
        content_type=file.content_type or "application/octet-stream",
        size_bytes=len(contents),
    )


@router.get("")
async def list_uploads(
    session_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
) -> list[UploadRecord]:
    return await session_service.list_uploads(session_id=session_id, requesting_user_id=user.user_id)


@router.get("/{upload_id}")
async def get_upload(
    session_id: str,
    upload_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
) -> UploadRecord:
    return await session_service.get_upload(
        session_id=session_id, upload_id=upload_id, requesting_user_id=user.user_id
    )
