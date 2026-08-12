"""Unit tests for ``PeerReviewService``.

Covers ``get_agent_assessments``'s per-step pending/reviewed/undetermined
handling, ``apply_selected_fixes``'s step_inputs construction using a fake
orchestrator that records the ``resume_workflow`` call it received, and
the pure ``_parse_agent_assessment``/``_count_code_blocks`` helpers
directly (no real LLM available in tests, same convention as
``tests/unit/services/test_requirements_service.py``).
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.workflow_models import WorkflowRunResult, WorkflowStepInput, WorkflowStepResult
from app.services.peer_review_service import (
    PeerReviewService,
    _count_code_blocks,
    _parse_agent_assessment,
)
from app.services.session_service import create_session_service
from app.services.workshop_service import UnknownWorkflowRunError


class _FakeOrchestrator:
    """Duck-typed stand-in recording the ``resume_workflow`` call it received."""

    def __init__(self, *, run: WorkflowRunResult | None) -> None:
        self._run = run
        self.resume_calls: list[dict] = []

    async def get_workflow_run(self, workflow_run_id: str) -> WorkflowRunResult | None:
        if self._run is None or workflow_run_id != self._run.workflow_run_id:
            return None
        return self._run

    async def resume_workflow(
        self,
        *,
        workflow_run_id: str,
        session_id: str,
        trace_id: str | None,
        step_inputs: dict[str, WorkflowStepInput],
    ) -> WorkflowRunResult:
        self.resume_calls.append(
            {
                "workflow_run_id": workflow_run_id,
                "session_id": session_id,
                "trace_id": trace_id,
                "step_inputs": step_inputs,
            }
        )
        assert self._run is not None
        return self._run


def _step_result(step_id: str, *, output_text: str) -> WorkflowStepResult:
    now = datetime.now(UTC)
    return WorkflowStepResult(
        step_id=step_id,
        agent_id="genie-orchestrator",
        status="completed",
        output_text=output_text,
        started_at=now,
        completed_at=now,
    )


@pytest.fixture
def blocked_run() -> WorkflowRunResult:
    return WorkflowRunResult(
        workflow_run_id="run-1",
        workflow_id="solution-discovery-workflow",
        session_id="session-1",
        status="waiting_for_approval",
        waves=[["build-solution"], ["security-assessment", "test-generation"]],
        step_results=[
            _step_result("build-solution", output_text="```tsx\nconst x = 1;\n```"),
            _step_result("security-assessment", output_text="SECURITY_GATE: FAIL\nFINDINGS:\nNone.\n"),
            _step_result("test-generation", output_text="TEST_COVERAGE_GATE: PASS\nFINDINGS:\nNone.\n"),
        ],
    )


def _make_service(orchestrator: _FakeOrchestrator, session_service) -> PeerReviewService:
    return PeerReviewService(
        orchestrator=orchestrator,  # type: ignore[arg-type]
        session_service=session_service,
        security_assessment_step_id="security-assessment",
        test_generation_step_id="test-generation",
        gated_step_ids=("security-assessment", "test-generation"),
    )


async def test_get_agent_assessments_returns_pending_when_steps_not_completed(
    blocked_run: WorkflowRunResult,
) -> None:
    run_without_gates = blocked_run.model_copy(update={"step_results": blocked_run.step_results[:1]})
    orchestrator = _FakeOrchestrator(run=run_without_gates)
    session_service = create_session_service(orchestrator=orchestrator)  # type: ignore[arg-type]
    session = await session_service.create_session(owner_user_id="user-1", title="t")
    service = _make_service(orchestrator, session_service)

    report = await service.get_agent_assessments(
        session_id=session.id, requesting_user_id="user-1", workflow_run_id="run-1"
    )

    assert report.security_assessment.status == "pending"
    assert report.test_generation.status == "pending"


async def test_get_agent_assessments_returns_reviewed_assessments_from_each_step(
    blocked_run: WorkflowRunResult,
) -> None:
    orchestrator = _FakeOrchestrator(run=blocked_run)
    session_service = create_session_service(orchestrator=orchestrator)  # type: ignore[arg-type]
    session = await session_service.create_session(owner_user_id="user-1", title="t")
    service = _make_service(orchestrator, session_service)

    report = await service.get_agent_assessments(
        session_id=session.id, requesting_user_id="user-1", workflow_run_id="run-1"
    )

    assert report.security_assessment.status == "reviewed"
    assert report.security_assessment.gate == "fail"
    assert report.security_assessment.assessed_by_agent_id == "genie-orchestrator"
    assert report.test_generation.status == "reviewed"
    assert report.test_generation.gate == "pass"


async def test_get_agent_assessments_raises_for_unknown_workflow_run(blocked_run: WorkflowRunResult) -> None:
    orchestrator = _FakeOrchestrator(run=blocked_run)
    session_service = create_session_service(orchestrator=orchestrator)  # type: ignore[arg-type]
    session = await session_service.create_session(owner_user_id="user-1", title="t")
    service = _make_service(orchestrator, session_service)

    with pytest.raises(UnknownWorkflowRunError):
        await service.get_agent_assessments(
            session_id=session.id, requesting_user_id="user-1", workflow_run_id="does-not-exist"
        )


async def test_apply_selected_fixes_re_runs_build_and_every_gated_step(
    blocked_run: WorkflowRunResult,
) -> None:
    orchestrator = _FakeOrchestrator(run=blocked_run)
    session_service = create_session_service(orchestrator=orchestrator)  # type: ignore[arg-type]
    session = await session_service.create_session(owner_user_id="user-1", title="t")
    service = _make_service(orchestrator, session_service)

    result = await service.apply_selected_fixes(
        session_id=session.id,
        requesting_user_id="user-1",
        workflow_run_id="run-1",
        selected_findings=["SQL injection in the search endpoint"],
        trace_id="trace-1",
    )

    assert result is blocked_run
    assert len(orchestrator.resume_calls) == 1
    call = orchestrator.resume_calls[0]
    assert call["workflow_run_id"] == "run-1"
    assert call["trace_id"] == "trace-1"
    assert set(call["step_inputs"].keys()) == {
        "build-solution",
        "security-assessment",
        "test-generation",
    }
    build_input = call["step_inputs"]["build-solution"]
    assert "SQL injection in the search endpoint" in build_input.variables["user_message"]
    for gated_step_id in ("security-assessment", "test-generation"):
        assert call["step_inputs"][gated_step_id].variables == {"user_message": ""}


async def test_apply_selected_fixes_with_no_selected_findings_sends_empty_instruction(
    blocked_run: WorkflowRunResult,
) -> None:
    orchestrator = _FakeOrchestrator(run=blocked_run)
    session_service = create_session_service(orchestrator=orchestrator)  # type: ignore[arg-type]
    session = await session_service.create_session(owner_user_id="user-1", title="t")
    service = _make_service(orchestrator, session_service)

    await service.apply_selected_fixes(
        session_id=session.id,
        requesting_user_id="user-1",
        workflow_run_id="run-1",
        selected_findings=[],
    )

    build_input = orchestrator.resume_calls[0]["step_inputs"]["build-solution"]
    assert build_input.variables == {"user_message": ""}


def test_count_code_blocks_counts_fence_pairs():
    text = "intro\n```ts\nconst x = 1;\n```\nmiddle\n```python\ndef f(): pass\n```\n"

    assert _count_code_blocks(text) == 2


def test_count_code_blocks_returns_zero_for_no_fences():
    assert _count_code_blocks("no code here") == 0


def test_parse_agent_assessment_security_reviewed_with_matching_findings_only():
    text = (
        "Some narrative.\n"
        "SECURITY_GATE: FAIL\n"
        "FINDINGS:\n"
        "- [security|critical|sec-1] SQL injection | Recommendation: Parameterize.\n"
        "- [test_coverage|low|test-1] Unrelated finding for a different gate\n"
    )

    assessment = _parse_agent_assessment(
        text,
        gate_field_name="security_gate",
        gate_name="security",
        assessed_by_agent_id="security-assessment-agent",
    )

    assert assessment.status == "reviewed"
    assert assessment.gate == "fail"
    assert assessment.summary == text
    assert assessment.assessed_by_agent_id == "security-assessment-agent"
    assert assessment.tests_generated == 0
    # Only the security-gate finding is kept, not the test_coverage one.
    assert len(assessment.findings) == 1
    assert assessment.findings[0].id == "sec-1"


def test_parse_agent_assessment_test_generation_counts_code_blocks():
    text = (
        "```ts\n// unit test\nexpect(1).toBe(1);\n```\n"
        "```ts\n// integration test\nexpect(2).toBe(2);\n```\n"
        "TEST_COVERAGE_GATE: PASS\n"
        "FINDINGS:\n"
        "None.\n"
    )

    assessment = _parse_agent_assessment(
        text,
        gate_field_name="test_coverage_gate",
        gate_name="test_coverage",
        assessed_by_agent_id="test-generation-agent",
        count_tests=True,
    )

    assert assessment.status == "reviewed"
    assert assessment.gate == "pass"
    assert assessment.tests_generated == 2
    assert assessment.findings == []


def test_parse_agent_assessment_returns_undetermined_when_no_gate_marker():
    text = "[local-agent-gateway] agent='security-assessment-agent' resolved_prompt_length=10"

    assessment = _parse_agent_assessment(
        text,
        gate_field_name="security_gate",
        gate_name="security",
        assessed_by_agent_id="security-assessment-agent",
    )

    assert assessment.status == "undetermined"
    assert assessment.gate is None
    assert assessment.summary == text
