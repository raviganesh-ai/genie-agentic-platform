"""Collaboration domain model.

Implements the "COLLABORATION MODEL" for Phase 6: agent-to-agent
collaboration, shared memory collaboration, recommendation dependencies,
and approval dependencies, rendered by the frontend Collaboration Graph in
a later phase. Every recorded ``CollaborationEvent`` also becomes a node/
edge in the Phase 5 ``DecisionGraph`` for the same session (see
``app.orchestration.collaboration_service``).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CollaborationType = Literal[
    "agent_to_agent",
    "shared_memory",
    "recommendation_dependency",
    "approval_dependency",
]

__all__ = ["CollaborationEvent", "CollaborationType"]


class CollaborationEvent(BaseModel):
    """A single recorded instance of agent collaboration."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    collaboration_type: CollaborationType
    session_id: str = Field(min_length=1)
    workflow_run_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    source_agent_id: str = Field(min_length=1)
    target_agent_id: str | None = None
    memory_reference: str | None = None
    recommendation_id: str | None = None
    approval_id: str | None = None
    detail: str = ""
    timestamp: datetime
