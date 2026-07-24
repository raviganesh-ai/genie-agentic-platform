"""Unit tests for the Phase 6 workflow state machine."""
from __future__ import annotations

import pytest

from app.orchestration.workflow_state_machine import (
    InvalidWorkflowTransitionError,
    WorkflowStateMachine,
)


def test_start_creates_pending_state() -> None:
    machine = WorkflowStateMachine.start(
        workflow_run_id="run-1", workflow_id="wf-1", session_id="session-1"
    )
    assert machine.state.status == "pending"
    assert machine.state.workflow_run_id == "run-1"


def test_valid_transition_sequence_reaches_completed() -> None:
    machine = WorkflowStateMachine.start(
        workflow_run_id="run-1", workflow_id="wf-1", session_id="session-1"
    )
    machine.transition("running")
    assert machine.state.status == "running"
    machine.transition("waiting_for_approval")
    assert machine.state.status == "waiting_for_approval"
    machine.transition("running")
    machine.transition("completed")
    assert machine.state.status == "completed"


def test_running_may_loop_to_running_for_multi_wave_progress() -> None:
    machine = WorkflowStateMachine.start(
        workflow_run_id="run-1", workflow_id="wf-1", session_id="session-1"
    )
    machine.transition("running")
    machine.transition("running")
    assert machine.state.status == "running"


@pytest.mark.parametrize(
    ("start_status", "target_status"),
    [
        ("pending", "completed"),
        ("completed", "running"),
        ("failed", "running"),
        ("waiting_for_approval", "completed"),
    ],
)
def test_invalid_transitions_are_rejected(start_status: str, target_status: str) -> None:
    machine = WorkflowStateMachine.start(
        workflow_run_id="run-1", workflow_id="wf-1", session_id="session-1"
    )
    # Drive the machine to start_status via a known-valid path where needed.
    if start_status != "pending":
        machine.transition("running")
        if start_status != "running":
            machine.transition(start_status)  # type: ignore[arg-type]

    with pytest.raises(InvalidWorkflowTransitionError):
        machine.transition(target_status)  # type: ignore[arg-type]


def test_transition_applies_extra_state_updates() -> None:
    machine = WorkflowStateMachine.start(
        workflow_run_id="run-1", workflow_id="wf-1", session_id="session-1"
    )
    machine.transition("running", detail="started")
    assert machine.state.detail == "started"
