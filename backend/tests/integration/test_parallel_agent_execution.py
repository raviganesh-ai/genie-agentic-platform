"""Integration tests verifying parallel execution wave behavior."""
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


async def test_parallel_steps_execute_concurrently_and_record_collaboration(
    orchestrator,
) -> None:
    result = await orchestrator.run_workflow(
        workflow_id="parallel-workflow",
        session_id="session-parallel",
        trace_id="trace-1",
        step_inputs={
            "step-a": WorkflowStepInput(step_id="step-a", variables={"x": "1"}),
            "step-b": WorkflowStepInput(step_id="step-b", variables={"y": "2"}),
        },
    )

    assert result.status == "waiting_for_approval"
    assert set(result.waves[0]) == {"step-a", "step-b"}

    run_id = result.workflow_run_id
    collaboration_events = orchestrator.collaboration_service.events_for_run(run_id)
    assert any(
        event.collaboration_type == "agent_to_agent"
        and {event.source_agent_id, event.target_agent_id} == {"agent-a", "agent-b"}
        for event in collaboration_events
    )


async def test_checkpoint_recorded_after_parallel_wave(orchestrator) -> None:
    result = await orchestrator.run_workflow(
        workflow_id="parallel-workflow",
        session_id="session-checkpoint",
        trace_id="trace-1",
        step_inputs={
            "step-a": WorkflowStepInput(step_id="step-a", variables={"x": "1"}),
            "step-b": WorkflowStepInput(step_id="step-b", variables={"y": "2"}),
        },
    )

    checkpoints = orchestrator.checkpoint_service.checkpoints_for_run(result.workflow_run_id)
    assert len(checkpoints) == 1
    assert set(checkpoints[0].completed_step_ids) == {"step-a", "step-b"}
    assert checkpoints[0].wave_index == 0
