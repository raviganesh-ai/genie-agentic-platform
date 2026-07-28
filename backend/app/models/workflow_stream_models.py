"""Live workflow step event model.

``WorkflowStreamEvent`` is the payload published to ``WorkflowEventBus``
(``app.orchestration.workflow_event_bus``) as each workflow step starts,
streams incremental output, completes, or fails - and the payload the SSE
route (``GET /sessions/{session_id}/workflow-events/stream``) serializes to
the client. This is a pure "is currently happening" live signal, never a
system of record: the durable audit trail remains
``GovernanceService``/``WorkflowExecutionService``.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["WorkflowStreamEvent", "WorkflowStreamEventType"]

WorkflowStreamEventType = Literal["step_started", "step_delta", "step_completed", "step_failed"]


class WorkflowStreamEvent(BaseModel):
    """One live event about a single workflow step's execution."""

    model_config = ConfigDict(extra="forbid")

    event_type: WorkflowStreamEventType
    session_id: str = Field(min_length=1)
    workflow_run_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    delta: str | None = None
    output_preview: str | None = None
    error: str | None = None
    emitted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
