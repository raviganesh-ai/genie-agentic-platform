"""Workflow state machine.

Implements "WORKFLOW STATE MACHINE" for Phase 6: the only allowed
transitions between ``WorkflowStatus`` values. Pure logic, no I/O - used by
``WorkflowRuntime`` to validate every state change it makes and by tests to
verify illegal transitions are rejected.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.models.workflow_state import WorkflowState, WorkflowStatus

__all__ = ["InvalidWorkflowTransitionError", "WorkflowStateMachine"]

# Every status a workflow run may move to from a given status. Terminal
# statuses ("failed", "completed") have no outgoing transitions.
_ALLOWED_TRANSITIONS: dict[WorkflowStatus, frozenset[WorkflowStatus]] = {
    "pending": frozenset({"running", "failed"}),
    "running": frozenset(
        {
            "waiting_for_agent",
            "waiting_for_approval",
            "waiting_for_proceed",
            "blocked",
            "failed",
            "completed",
            "running",
        }
    ),
    "waiting_for_agent": frozenset({"running", "failed", "blocked"}),
    "waiting_for_approval": frozenset({"running", "blocked", "failed"}),
    "waiting_for_proceed": frozenset({"running", "failed"}),
    "blocked": frozenset({"running", "failed"}),
    "failed": frozenset(),
    "completed": frozenset(),
}


class InvalidWorkflowTransitionError(RuntimeError):
    """Raised when a workflow run attempts an illegal status transition."""


class WorkflowStateMachine:
    """Validates and applies ``WorkflowState`` transitions for one workflow run."""

    def __init__(self, state: WorkflowState) -> None:
        self._state = state

    @property
    def state(self) -> WorkflowState:
        return self._state

    @classmethod
    def start(
        cls, *, workflow_run_id: str, workflow_id: str, session_id: str
    ) -> WorkflowStateMachine:
        return cls(
            WorkflowState(
                workflow_run_id=workflow_run_id,
                workflow_id=workflow_id,
                session_id=session_id,
                status="pending",
                updated_at=datetime.now(UTC),
            )
        )

    def can_transition(self, new_status: WorkflowStatus) -> bool:
        return new_status in _ALLOWED_TRANSITIONS[self._state.status]

    def transition(self, new_status: WorkflowStatus, **updates: object) -> WorkflowState:
        """Move to ``new_status``, applying any additional ``WorkflowState`` field updates.

        Raises ``InvalidWorkflowTransitionError`` (fail closed) if the
        transition is not allowed from the current status.
        """

        if not self.can_transition(new_status):
            raise InvalidWorkflowTransitionError(
                f"Cannot transition workflow run '{self._state.workflow_run_id}' "
                f"from '{self._state.status}' to '{new_status}'."
            )
        self._state = self._state.model_copy(
            update={"status": new_status, "updated_at": datetime.now(UTC), **updates}
        )
        return self._state
