"""Workflow synchronization checkpoint model.

Implements the "PARALLEL EXECUTION" synchronization checkpoint requirement
for Phase 6: a durable record of every point at which the runtime
synchronized a parallel execution group (wave) before continuing to the
next one. Held in-process by
``app.orchestration.workflow_checkpoint_service.WorkflowCheckpointService``
for Phase 6 (a durable backend is a later-phase concern, mirroring how
``DecisionGraphService`` is held in-process for Phase 5).
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["WorkflowCheckpoint"]


class WorkflowCheckpoint(BaseModel):
    """A single synchronization point reached after one execution wave completes."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    workflow_run_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    wave_index: int = Field(ge=0)
    completed_step_ids: list[str] = Field(default_factory=list)
    created_at: datetime
