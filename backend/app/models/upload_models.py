"""Upload / ingestion domain models.

Implements the "Upload & Ingestion" requirement for Phase 7: transcripts,
audio, video, and supporting documents can be uploaded to a session and
tracked through ingestion. No transcription/ingestion pipeline is
implemented here (that is a later-phase concern, ``app.transcription`` per
the module list in ``.github/copilot-instructions.md``) - this module only
models the upload record and its ingestion status lifecycle.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

UploadType = Literal["transcript", "audio", "video", "supporting_document"]
IngestionStatus = Literal["received", "queued", "processing", "completed", "failed"]

__all__ = ["IngestionStatus", "UploadMetadata", "UploadRecord", "UploadType"]


class UploadMetadata(BaseModel):
    """Public metadata for a single uploaded file and its ingestion status."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    upload_type: UploadType
    file_name: str = Field(min_length=1)
    content_type: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    uploaded_by: str = Field(min_length=1)
    status: IngestionStatus = "received"
    detail: str = ""
    uploaded_at: datetime
    updated_at: datetime


class UploadRecord(UploadMetadata):
    """Internal upload record including extracted text used by downstream agents."""

    transcript_text: str | None = Field(
        default=None,
        description=(
            "Text content of this upload once available - decoded directly for "
            "'transcript' uploads, produced by Azure AI Speech for 'audio'/'video' "
            "uploads. None until ingestion completes."
        ),
    )
