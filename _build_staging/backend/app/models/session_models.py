"""Session domain model.

A ``Session`` is the top-level container Phase 7 introduces for a single
customer engagement: everything else (uploads, workflow runs, mission
control state, workshop interactions, outputs) is scoped to one session id.
No prior phase modeled sessions as a first-class persisted entity - Phase 6
and earlier only ever received a ``session_id`` string from callers.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SessionStatus = Literal["created", "active", "completed", "archived"]

__all__ = ["Session", "SessionStatus"]


class Session(BaseModel):
    """A single customer engagement session, owned by exactly one user."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    owner_user_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    status: SessionStatus = "created"
    created_at: datetime
    updated_at: datetime
    latest_workflow_run_id: str | None = None
