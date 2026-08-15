"""Transcription domain models."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["TranscriptionResult"]


class TranscriptionResult(BaseModel):
    """The text (and optional metadata) produced by transcribing one recording."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(default="")
    language: str | None = None
    duration_seconds: float | None = None
