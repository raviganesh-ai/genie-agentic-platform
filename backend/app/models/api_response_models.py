"""Generic, cross-cutting API response models shared across Phase 7 routers."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["ErrorResponse", "MessageResponse"]


class ErrorResponse(BaseModel):
    """A uniform error envelope returned by every API router.

    Never includes secrets, stack traces, or internal implementation
    detail, per "Errors, logs, and health endpoints must never expose
    secrets" in ``.github/copilot-instructions.md``.
    """

    model_config = ConfigDict(extra="forbid")

    detail: str = Field(min_length=1)


class MessageResponse(BaseModel):
    """A uniform acknowledgement envelope for actions with no richer response body."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1)
