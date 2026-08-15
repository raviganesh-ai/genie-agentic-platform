"""Workflow checkpoint service.

Implements the "PARALLEL EXECUTION" synchronization checkpoint requirement
for Phase 6: after every execution wave (sequential step or parallel
group) completes, ``WorkflowRuntime`` records a ``WorkflowCheckpoint`` here
before continuing to the next wave. Held in-process per workflow run,
mirroring how ``DecisionGraphService`` holds graphs in-process for Phase 5.
"""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.models.workflow_checkpoint import WorkflowCheckpoint

__all__ = ["WorkflowCheckpointService"]


class WorkflowCheckpointService:
    """Records and queries synchronization checkpoints for workflow runs."""

    def __init__(self) -> None:
        self._checkpoints: dict[str, list[WorkflowCheckpoint]] = {}

    def record_checkpoint(
        self,
        *,
        workflow_run_id: str,
        session_id: str,
        wave_index: int,
        completed_step_ids: list[str],
    ) -> WorkflowCheckpoint:
        checkpoint = WorkflowCheckpoint(
            id=str(uuid4()),
            workflow_run_id=workflow_run_id,
            session_id=session_id,
            wave_index=wave_index,
            completed_step_ids=list(completed_step_ids),
            created_at=datetime.now(UTC),
        )
        self._checkpoints.setdefault(workflow_run_id, []).append(checkpoint)
        return checkpoint

    def checkpoints_for_run(self, workflow_run_id: str) -> list[WorkflowCheckpoint]:
        return list(self._checkpoints.get(workflow_run_id, []))

    def latest_checkpoint(self, workflow_run_id: str) -> WorkflowCheckpoint | None:
        checkpoints = self._checkpoints.get(workflow_run_id)
        return checkpoints[-1] if checkpoints else None
