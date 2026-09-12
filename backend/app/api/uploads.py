"""Upload API routes: transcript/audio/video/supporting-document uploads.

Thin wrapper over ``SessionService.register_upload`` / ``list_uploads`` /
``get_upload``. ``transcript``/``supporting_document`` uploads have their
text extracted through the configured ``DocumentUnderstandingService``;
``audio``/``video`` uploads are transcribed synchronously via the configured
``SpeechToTextService`` (Azure AI Speech in production, per
``app.transcription.speech_service``) before the upload record is created.
File bytes themselves are still never persisted to disk/blob storage - only
the resulting transcript text, which is what every downstream agent needs.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response, UploadFile, status

from app.api.dependencies import (
    get_discovery_service,
    get_document_understanding_service,
    get_session_service,
    get_speech_to_text_service,
)
from app.discovery.service import DiscoveryService
from app.models.upload_models import UploadMetadata, UploadType
from app.security.auth_models import AuthenticatedUser
from app.security.dependencies import get_current_user
from app.services.document_understanding_service import (
    DocumentUnderstandingError,
    DocumentUnderstandingService,
)
from app.services.session_service import SessionService
from app.transcription.speech_service import SpeechToTextError, SpeechToTextService

router = APIRouter(prefix="/sessions/{session_id}/uploads", tags=["uploads"])


@router.post("/{upload_type}", status_code=201)
async def create_upload(
    session_id: str,
    upload_type: UploadType,
    file: UploadFile,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
    document_service: DocumentUnderstandingService = Depends(get_document_understanding_service),
    speech_service: SpeechToTextService = Depends(get_speech_to_text_service),
) -> UploadMetadata:
    contents = await file.read()
    file_name = file.filename or "unnamed"
    content_type = file.content_type or "application/octet-stream"

    transcript_text: str | None = None
    status: str = "received"
    detail = ""

    if upload_type in ("transcript", "supporting_document"):
        try:
            transcript_text = await document_service.extract(
                content=contents, content_type=content_type, file_name=file_name
            )
            status = "completed"
        except DocumentUnderstandingError as exc:
            status = "failed"
            detail = str(exc)
    elif upload_type in ("audio", "video"):
        try:
            result = await speech_service.transcribe(
                audio_bytes=contents, content_type=content_type, file_name=file_name
            )
            transcript_text = result.text
            status = "completed"
        except SpeechToTextError as exc:
            status = "failed"
            detail = str(exc)

    return await session_service.register_upload(
        session_id=session_id,
        requesting_user_id=user.user_id,
        upload_type=upload_type,
        file_name=file_name,
        content_type=content_type,
        size_bytes=len(contents),
        status=status,
        detail=detail,
        transcript_text=transcript_text,
    )


@router.get("")
async def list_uploads(
    session_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
) -> list[UploadMetadata]:
    return await session_service.list_uploads(session_id=session_id, requesting_user_id=user.user_id)


@router.get("/{upload_id}")
async def get_upload(
    session_id: str,
    upload_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
) -> UploadMetadata:
    return await session_service.get_upload(
        session_id=session_id, upload_id=upload_id, requesting_user_id=user.user_id
    )


@router.delete("/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_upload(
    session_id: str,
    upload_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
    discovery_service: DiscoveryService = Depends(get_discovery_service),
) -> Response:
    await session_service.get_upload(
        session_id=session_id,
        upload_id=upload_id,
        requesting_user_id=user.user_id,
    )
    await discovery_service.remove_source_upload(
        session_id=session_id,
        upload_id=upload_id,
        requesting_user_id=user.user_id,
    )
    await session_service.delete_upload(
        session_id=session_id,
        upload_id=upload_id,
        requesting_user_id=user.user_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
