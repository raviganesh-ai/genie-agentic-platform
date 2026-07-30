"""Unit tests for ``ServicePolicyService``.

Mirrors ``tests/unit/services/test_peer_review_service.py``'s
``_FakeOrchestrator`` approach exactly (no real LLM available in tests),
but exercises the full real ``ApprovalService``/``GovernanceService``/
``MemoryAccessPolicyService`` stack so every field returned is verified to
come from an actually loaded, actually enforced policy document - never a
fabricated value.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.governance.approval_service import ApprovalPolicyDocument, ApprovalService
from app.governance.governance_service import create_governance_service
from app.memory.memory_access_policy_service import MemoryAccessPolicyService
from app.models.approval_models import ApprovalCheckpoint
from app.models.workflow_models import WorkflowRunResult, WorkflowStepResult
from app.repositories.approval_repository import InMemoryApprovalRepository
from app.services.service_policy_service import (
    ServicePolicyService,
    _extract_access_control_summary,
)
from app.services.session_service import create_session_service
from app.services.workshop_service import UnknownWorkflowRunError


def test_extract_access_control_summary_reads_security_review_line():
    text = (
        "SECURITY_REVIEW: Access is restricted to least-privilege managed "
        "identities; secrets come from Key Vault.\n"
        "GOVERNANCE_DECISION: APPROVED\n"
        "SECURITY_GATE: PASS\n"
    )

    assert _extract_access_control_summary(text) == (
        "Access is restricted to least-privilege managed identities; secrets "
        "come from Key Vault."
    )


def test_extract_access_control_summary_returns_empty_when_no_marker_present():
    assert _extract_access_control_summary("[local-agent-gateway] resolved_prompt_length=42") == ""


class _FakeOrchestrator:
    """Duck-typed stand-in returning a fixed workflow run, mirroring
    ``test_peer_review_service.py``'s ``_FakeOrchestrator``."""

    def __init__(self, *, run: WorkflowRunResult | None) -> None:
        self._run = run

    def get_workflow_run(self, workflow_run_id: str) -> WorkflowRunResult | None:
        if self._run is None or workflow_run_id != self._run.workflow_run_id:
            return None
        return self._run


def _step_result(step_id: str, *, output_text: str, status: str = "completed") -> WorkflowStepResult:
    now = datetime.now(UTC)
    return WorkflowStepResult(
        step_id=step_id,
        agent_id="governance-reviewer",
        status=status,
        output_text=output_text,
        started_at=now,
        completed_at=now,
    )


def _run(step_results: list[WorkflowStepResult]) -> WorkflowRunResult:
    return WorkflowRunResult(
        workflow_run_id="run-1",
        workflow_id="solution-discovery-workflow",
        session_id="session-1",
        status="waiting_for_approval",
        waves=[["build-solution"], ["governance-review"]],
        step_results=step_results,
    )


@pytest.fixture
def approval_policy() -> ApprovalPolicyDocument:
    return ApprovalPolicyDocument(
        default_expiry_minutes=1440,
        checkpoints=[
            ApprovalCheckpoint(
                id="build-review-approval",
                name="Build Review Approval",
                description="Human approval of the Build Agent's generated code.",
            ),
            ApprovalCheckpoint(
                id="final-output-approval",
                name="Final Output Approval",
                description="Human approval of the final customer-facing output.",
            ),
        ],
    )


class _Fixture:
    """Groups everything one test needs: the service under test, its
    approval_service (so a test can raise approval requests), and a
    pre-created session id to call ``get_service_policy`` against."""

    def __init__(
        self, *, service: ServicePolicyService, approval_service: ApprovalService, session_id: str
    ) -> None:
        self.service = service
        self.approval_service = approval_service
        self.session_id = session_id


async def _build_fixture(
    *, run: WorkflowRunResult, local_settings, approval_policy: ApprovalPolicyDocument
) -> _Fixture:
    orchestrator = _FakeOrchestrator(run=run)
    session_service = create_session_service(orchestrator=orchestrator)  # type: ignore[arg-type]
    session = await session_service.create_session(owner_user_id="user-1", title="t")
    governance_service = create_governance_service(settings=local_settings)
    approval_service = ApprovalService(
        repository=InMemoryApprovalRepository(),
        policy=approval_policy,
        governance_service=governance_service,
    )
    memory_policy_service = MemoryAccessPolicyService.load(local_settings.policies_path)
    service = ServicePolicyService(
        orchestrator=orchestrator,  # type: ignore[arg-type]
        session_service=session_service,
        approval_service=approval_service,
        governance_service=governance_service,
        memory_policy_service=memory_policy_service,
        governance_review_step_id="governance-review",
    )
    return _Fixture(service=service, approval_service=approval_service, session_id=session.id)


async def test_get_service_policy_is_pending_before_governance_review_completes(
    local_settings, approval_policy
):
    run = _run([_step_result("build-solution", output_text="```tsx\nconst x = 1;\n```")])
    fixture = await _build_fixture(run=run, local_settings=local_settings, approval_policy=approval_policy)

    policy = await fixture.service.get_service_policy(
        session_id=fixture.session_id, requesting_user_id="user-1", workflow_run_id="run-1"
    )

    assert policy.status == "pending"
    assert policy.access_control_summary == ""
    assert policy.assessed_by_agent_id is None


async def test_get_service_policy_reports_checkpoint_statuses_and_policy_documents(
    local_settings, approval_policy
):
    run = _run(
        [
            _step_result("build-solution", output_text="```tsx\nconst x = 1;\n```"),
            _step_result(
                "governance-review",
                output_text=(
                    "SECURITY_REVIEW: Least-privilege roles only; no secrets embedded.\n"
                    "GOVERNANCE_DECISION: APPROVED\n"
                    "SECURITY_GATE: PASS\nTEST_COVERAGE_GATE: PASS\n"
                    "ARCHITECTURE_GATE: PASS\nCODE_QUALITY_GATE: PASS\n"
                    "FINDINGS:\nNone.\nPEER_REVIEW_DECISION: APPROVED\n"
                ),
            ),
        ]
    )
    fixture = await _build_fixture(run=run, local_settings=local_settings, approval_policy=approval_policy)

    await fixture.approval_service.request_approval(
        checkpoint_id="build-review-approval",
        session_id=fixture.session_id,
        trace_id="trace-1",
        requested_by_agent_id="genie-orchestrator",
        subject_type="workflow_step",
        subject_id="build-solution",
    )

    policy = await fixture.service.get_service_policy(
        session_id=fixture.session_id, requesting_user_id="user-1", workflow_run_id="run-1"
    )

    assert policy.status == "ready"
    assert policy.assessed_by_agent_id == "governance-reviewer"
    assert policy.access_control_summary == "Least-privilege roles only; no secrets embedded."

    checkpoints_by_id = {c.checkpoint_id: c for c in policy.deployment_checkpoints}
    assert checkpoints_by_id["build-review-approval"].status == "pending"
    assert checkpoints_by_id["final-output-approval"].status == "not_reached"

    assert policy.governance_tracking.track_executions is True
    assert policy.decision_lineage.require_evidence_references is True
    assert policy.session_replay.enabled is True
    assert policy.memory_access_policy.personal_agent_memory.accessible_by == "owning_agent"


async def test_get_service_policy_raises_for_unknown_workflow_run(local_settings, approval_policy):
    run = _run([_step_result("build-solution", output_text="```tsx\nconst x = 1;\n```")])
    fixture = await _build_fixture(run=run, local_settings=local_settings, approval_policy=approval_policy)

    with pytest.raises(UnknownWorkflowRunError):
        await fixture.service.get_service_policy(
            session_id=fixture.session_id, requesting_user_id="user-1", workflow_run_id="does-not-exist"
        )
