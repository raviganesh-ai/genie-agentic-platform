"""Integration tests for RequirementsService's "pending"/"undetermined"
and unknown-run states, using a real ``AgentOrchestrator`` running the
hermetic Phase 6 fixture workflows through a deterministic test gateway.

"qualified"/"not_qualified" states depend on an agent's own free-text
reasoning, which ``LocalAgentGateway``'s deterministic stub output never
produces - those are covered by pure unit tests of ``_parse_verdict`` in
``tests/unit/services/test_requirements_service.py`` instead.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.models.workflow_models import WorkflowStepInput
from app.services.requirements_service import create_requirements_service
from app.services.session_service import create_session_service
from app.services.workshop_service import UnknownWorkflowRunError

from ._orchestration_helpers import create_test_agent_orchestrator


@pytest.fixture
def orchestrator(tmp_path: Path):
    return create_test_agent_orchestrator(tmp_path / "config")


async def _make_session(orchestrator, owner_user_id: str = "user-1"):
    session_service = create_session_service(orchestrator=orchestrator)
    session = await session_service.create_session(owner_user_id=owner_user_id, title="t")
    return session.id, session_service


async def test_returns_pending_when_qualification_step_has_not_run(orchestrator) -> None:
    session_id, session_service = await _make_session(orchestrator)
    run = await orchestrator.run_workflow(
        workflow_id="parallel-workflow",
        session_id=session_id,
        trace_id="trace-1",
        step_inputs={
            "step-a": WorkflowStepInput(step_id="step-a", variables={"x": "x-value"}),
            "step-b": WorkflowStepInput(step_id="step-b", variables={"y": "y-value"}),
        },
    )
    service = create_requirements_service(
        orchestrator=orchestrator,
        session_service=session_service,
        qualification_step_id="analyze-requirements",
    )

    result = await service.get_qualification(
        session_id=session_id, requesting_user_id="user-1", workflow_run_id=run.workflow_run_id
    )

    assert result.status == "pending"
    assert result.reason is None


async def test_returns_undetermined_when_step_output_has_no_verdict(orchestrator) -> None:
    session_id, session_service = await _make_session(orchestrator)
    run = await orchestrator.run_workflow(
        workflow_id="transcript-workflow",
        session_id=session_id,
        trace_id="trace-1",
        transcript_text="Customer call transcript.",
    )
    service = create_requirements_service(
        orchestrator=orchestrator,
        session_service=session_service,
        qualification_step_id="step-transcript",
    )

    result = await service.get_qualification(
        session_id=session_id, requesting_user_id="user-1", workflow_run_id=run.workflow_run_id
    )

    assert result.status == "undetermined"
    assert result.assessed_by_agent_id == "agent-a"


async def test_raises_for_unknown_workflow_run_id(orchestrator) -> None:
    session_id, session_service = await _make_session(orchestrator)
    service = create_requirements_service(
        orchestrator=orchestrator,
        session_service=session_service,
        qualification_step_id="analyze-requirements",
    )

    with pytest.raises(UnknownWorkflowRunError):
        await service.get_qualification(
            session_id=session_id, requesting_user_id="user-1", workflow_run_id="unknown-run"
        )


# Genie platform-change Phase D: RequirementsService.get_open_decisions
# mirrors get_qualification's own pending/undetermined/unknown-run states -
# uses the same real AgentOrchestrator running the hermetic Phase 6 fixture
# workflows through the deterministic test gateway (LocalAgentGateway never
# produces a real "Open Decisions" section, so "has_open_decisions" is
# covered by the pure _parse_open_decisions unit tests instead).
async def test_open_decisions_returns_pending_when_step_has_not_run(orchestrator) -> None:
    session_id, session_service = await _make_session(orchestrator)
    run = await orchestrator.run_workflow(
        workflow_id="parallel-workflow",
        session_id=session_id,
        trace_id="trace-1",
        step_inputs={
            "step-a": WorkflowStepInput(step_id="step-a", variables={"x": "x-value"}),
            "step-b": WorkflowStepInput(step_id="step-b", variables={"y": "y-value"}),
        },
    )
    service = create_requirements_service(
        orchestrator=orchestrator,
        session_service=session_service,
        qualification_step_id="analyze-requirements",
    )

    result = await service.get_open_decisions(
        session_id=session_id, requesting_user_id="user-1", workflow_run_id=run.workflow_run_id
    )

    assert result.status == "pending"
    assert result.open_decisions == []


async def test_open_decisions_returns_none_found_when_step_output_has_no_heading(
    orchestrator,
) -> None:
    session_id, session_service = await _make_session(orchestrator)
    run = await orchestrator.run_workflow(
        workflow_id="transcript-workflow",
        session_id=session_id,
        trace_id="trace-1",
        transcript_text="Customer call transcript.",
    )
    service = create_requirements_service(
        orchestrator=orchestrator,
        session_service=session_service,
        qualification_step_id="step-transcript",
    )

    result = await service.get_open_decisions(
        session_id=session_id, requesting_user_id="user-1", workflow_run_id=run.workflow_run_id
    )

    assert result.status == "none_found"
    assert result.open_decisions == []
    assert result.assessed_by_agent_id == "agent-a"


async def test_open_decisions_raises_for_unknown_workflow_run_id(orchestrator) -> None:
    session_id, session_service = await _make_session(orchestrator)
    service = create_requirements_service(
        orchestrator=orchestrator,
        session_service=session_service,
        qualification_step_id="analyze-requirements",
    )

    with pytest.raises(UnknownWorkflowRunError):
        await service.get_open_decisions(
            session_id=session_id, requesting_user_id="user-1", workflow_run_id="unknown-run"
        )
