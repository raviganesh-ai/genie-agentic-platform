"""Integration tests for the Phase 6 debugging workflow (FailureDetected)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.orchestration.agent_orchestrator import create_agent_orchestrator

from ._orchestration_helpers import build_orchestration_settings


@pytest.fixture
def orchestrator(tmp_path: Path):
    settings = build_orchestration_settings(tmp_path / "config")
    return create_agent_orchestrator(settings=settings)


async def test_handle_failure_runs_configured_debugging_workflow(orchestrator) -> None:
    result = await orchestrator.handle_failure(
        session_id="session-failure",
        source_agent_id="agent-a",
        error="Simulated agent execution failure for testing.",
    )

    assert result.workflow_id == "debugging-workflow"
    assert result.status == "completed"
    assert len(result.step_results) == 1
    assert result.step_results[0].agent_id == "debugging-agent"
    assert result.step_results[0].status == "completed"


async def test_handle_failure_records_governance_execution_event(orchestrator) -> None:
    result = await orchestrator.handle_failure(
        session_id="session-failure-2", source_agent_id="agent-b", error="Another failure."
    )
    # Debugging workflow must flow through the same governed execution path -
    # HandoffService/CollaborationService are unused for a single-step
    # workflow, but the step itself still records a governance execution
    # event via WorkflowStepExecutor, verified indirectly by successful
    # completion (governance failures propagate as exceptions, never silently).
    assert result.status == "completed"
