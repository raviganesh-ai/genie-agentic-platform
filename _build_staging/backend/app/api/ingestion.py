"""Ingestion status API routes.

Distinct from ``app.api.uploads`` (which accepts new uploads): this router
exposes and updates the ingestion pipeline status of an already-uploaded
file. Updating status is expected to be called by the ingestion pipeline
itself (a later-phase, ``app.transcription``-owned worker) - it still goes
through the same authenticated, session-owner-checked path as every other
Phase 7 route.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import get_session_service
from app.models.upload_models import IngestionStatus, UploadRecord
from app.security.auth_models import AuthenticatedUser
from app.security.dependencies import get_current_user
from app.services.session_service import SessionService

router = APIRouter(prefix="/sessions/{session_id}/ingestion", tags=["ingestion"])


class UpdateIngestionStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: IngestionStatus
    detail: str = Field(default="")


@router.get("/{upload_id}")
async def get_ingestion_status(
    session_id: str,
    upload_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
) -> UploadRecord:
    return await session_service.get_upload(
        session_id=session_id, upload_id=upload_id, requesting_user_id=user.user_id
    )


@router.patch("/{upload_id}")
async def update_ingestion_status(
    session_id: str,
    upload_id: str,
    body: UpdateIngestionStatusRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
) -> UploadRecord:
    return await session_service.update_ingestion_status(
        session_id=session_id,
        upload_id=upload_id,
        requesting_user_id=user.user_id,
        status=body.status,
        detail=body.detail,
    )
