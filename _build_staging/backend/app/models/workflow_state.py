"""Workflow runtime state model.

Implements "WORKFLOW STATE MACHINE" for Phase 6: the set of statuses a
single workflow run can be in, and the current snapshot of that state.
State *transitions* are enforced by
``app.orchestration.workflow_state_machine.WorkflowStateMachine``; this
module only defines the data shape.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

WorkflowStatus = Literal[
    "pending",
    "running",
    "waiting_for_agent",
    "waiting_for_approval",
    "waiting_for_proceed",
    "blocked",
    "failed",
    "completed",
]

__all__ = ["WorkflowState", "WorkflowStatus"]


class WorkflowState(BaseModel):
    """The current runtime status snapshot of a single workflow run."""

    model_config = ConfigDict(extra="forbid")

    workflow_run_id: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    status: WorkflowStatus
    current_wave_index: int = Field(default=0, ge=0)
    active_step_ids: list[str] = Field(default_factory=list)
    completed_step_ids: list[str] = Field(default_factory=list)
    failed_step_ids: list[str] = Field(default_factory=list)
    detail: str = ""
    updated_at: datetime
