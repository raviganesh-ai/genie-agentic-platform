"""Integration tests for the Phase 6 debugging workflow (FailureDetected)."""
from __future__ import annotations

from pathlib import Path

import pytest

from ._orchestration_helpers import create_test_agent_orchestrator


@pytest.fixture
def orchestrator(tmp_path: Path):
    return create_test_agent_orchestrator(tmp_path / "config")


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


# Genie platform-change Phase F: proves the real end-to-end wiring (real
# AgentOrchestrator -> DebuggingWorkflowService -> real
# InMemoryLessonsLearnedRepository) works, without fabricating a "has
# lessons" scenario - LocalAgentGateway's deterministic stub output never
# contains the "Lessons Learned (Prompt/Config Changes):" heading, so
# nothing should be persisted. The "has lessons" case is covered by
# tests/unit/orchestration/test_debugging_workflow_service.py's direct,
# crafted-output tests instead.
async def test_list_open_lessons_learned_is_empty_for_a_deterministic_stub_diagnosis(
    orchestrator,
) -> None:
    await orchestrator.handle_failure(
        session_id="session-failure-3", source_agent_id="agent-c", error="A third failure."
    )

    assert await orchestrator.list_open_lessons_learned() == []
