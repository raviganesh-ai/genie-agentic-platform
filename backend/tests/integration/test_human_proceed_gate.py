"""Integration tests for the `requires_human_proceed` structural pause gate.

Distinct from `requires_approval_checkpoint` (see
``test_approval_checkpoint_flow.py``): there is no ApprovalService, no
ApprovalRequest to create/decide/reject - the run simply pauses with status
``waiting_for_proceed`` until a resume call's own ``step_inputs`` explicitly
names the gated step, exactly as Genie's real Requirements -> Architecture ->
Code pages do when the human clicks their own "Proceed" action.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.models.workflow_models import WorkflowStepInput
from app.orchestration.agent_orchestrator import create_agent_orchestrator

from ._orchestration_helpers import build_orchestration_settings


@pytest.fixture
def orchestrator(tmp_path: Path):
    settings = build_orchestration_settings(tmp_path / "config")
    return create_agent_orchestrator(settings=settings)


async def test_workflow_pauses_at_waiting_for_proceed_without_being_targeted(orchestrator) -> None:
    result = await orchestrator.run_workflow(
        workflow_id="human-proceed-workflow",
        session_id="session-proceed-pause",
        trace_id="trace-1",
        step_inputs={"step-p": WorkflowStepInput(step_id="step-p", variables={"x": "1"})},
    )

    assert result.status == "waiting_for_proceed"
    assert any(r.step_id == "step-p" and r.status == "completed" for r in result.step_results)
    assert not any(r.step_id == "step-q" for r in result.step_results)


async def test_workflow_completes_once_the_next_step_is_explicitly_targeted(orchestrator) -> None:
    result = await orchestrator.run_workflow(
        workflow_id="human-proceed-workflow",
        session_id="session-proceed-resume",
        trace_id="trace-1",
        step_inputs={"step-p": WorkflowStepInput(step_id="step-p", variables={"x": "1"})},
    )
    assert result.status == "waiting_for_proceed"

    resumed = await orchestrator.resume_workflow(
        workflow_run_id=result.workflow_run_id,
        session_id="session-proceed-resume",
        trace_id="trace-2",
        step_inputs={"step-q": WorkflowStepInput(step_id="step-q", variables={"y": "2"})},
    )

    assert resumed.status == "completed"
    assert any(r.step_id == "step-q" and r.status == "completed" for r in resumed.step_results)
