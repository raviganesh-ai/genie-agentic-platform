"""Integration tests for end-to-end orchestrator workflow execution."""
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


async def test_run_workflow_executes_parallel_steps_and_pauses_for_approval(
    orchestrator,
) -> None:
    result = await orchestrator.run_workflow(
        workflow_id="parallel-workflow",
        session_id="session-1",
        trace_id="trace-1",
        step_inputs={
            "step-a": WorkflowStepInput(step_id="step-a", variables={"x": "1"}),
            "step-b": WorkflowStepInput(step_id="step-b", variables={"y": "2"}),
        },
    )

    assert result.status == "waiting_for_approval"
    completed_step_ids = {r.step_id for r in result.step_results if r.status == "completed"}
    assert completed_step_ids == {"step-a", "step-b"}
    assert result.waves[0] == ["step-a", "step-b"] or set(result.waves[0]) == {"step-a", "step-b"}


async def test_run_workflow_completes_after_approval_is_granted(orchestrator) -> None:
    result = await orchestrator.run_workflow(
        workflow_id="parallel-workflow",
        session_id="session-2",
        trace_id="trace-1",
        step_inputs={
            "step-a": WorkflowStepInput(step_id="step-a", variables={"x": "1"}),
            "step-b": WorkflowStepInput(step_id="step-b", variables={"y": "2"}),
        },
    )
    assert result.status == "waiting_for_approval"

    requests = await orchestrator.approval_service.list_requests_for_session("session-2")
    assert len(requests) == 1
    await orchestrator.approval_service.decide(
        request_id=requests[0].id, decision="approved", decided_by="reviewer-1"
    )

    resumed = await orchestrator.resume_workflow(
        workflow_run_id=result.workflow_run_id, session_id="session-2", trace_id="trace-2"
    )

    assert resumed.status == "completed"
    assert {r.step_id for r in resumed.step_results} == {"step-a", "step-b", "step-c"}
