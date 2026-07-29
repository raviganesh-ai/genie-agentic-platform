"""Unit tests for the Peer Review deploy-gate hardening in ``app.api.approvals``.

Exercises ``_enforce_peer_review_gate`` directly with duck-typed fakes
(mirroring ``tests/unit/services/test_peer_review_service.py``'s
``_FakeOrchestrator`` approach) rather than a full API round trip, since
driving the real solution-discovery-workflow all the way to the
final-output-approval checkpoint would require a live agent gateway. This
still exercises the exact production code path: only the checkpoint id
check, the ``workflow_run_id`` requirement, the gate-report lookup, and
the risk-acceptance fallback are under test here.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.api.approvals import _enforce_peer_review_gate
from app.models.approval_models import ApprovalRequest
from app.models.governance_gate_report import GovernanceGateReport
from app.services.peer_review_service import PeerReviewGateBlockedError


class _FakeApprovalService:
    def __init__(self, *, request: ApprovalRequest | None) -> None:
        self._request = request

    async def get_request(self, request_id: str) -> ApprovalRequest | None:
        return self._request


class _FakePeerReviewService:
    def __init__(self, *, report: GovernanceGateReport) -> None:
        self._report = report
        self.calls: list[dict] = []

    async def get_gate_report(
        self, *, session_id: str, requesting_user_id: str, workflow_run_id: str
    ) -> GovernanceGateReport:
        self.calls.append(
            {
                "session_id": session_id,
                "requesting_user_id": requesting_user_id,
                "workflow_run_id": workflow_run_id,
            }
        )
        return self._report


class _FakeGovernanceService:
    def __init__(self, *, accepted: bool) -> None:
        self._accepted = accepted
        self.calls: list[dict] = []

    async def has_risk_acceptance(self, *, session_id: str, workflow_run_id: str) -> bool:
        self.calls.append({"session_id": session_id, "workflow_run_id": workflow_run_id})
        return self._accepted


def _approval_request(checkpoint_id: str) -> ApprovalRequest:
    now = datetime.now(UTC)
    return ApprovalRequest(
        id="req-1",
        checkpoint_id=checkpoint_id,
        session_id="session-1",
        trace_id="trace-1",
        requested_by_agent_id="genie-orchestrator",
        subject_type="workflow_step",
        subject_id="deploy-solution",
        requested_at=now,
    )


async def test_non_final_output_checkpoint_is_not_gated() -> None:
    approval_service = _FakeApprovalService(request=_approval_request("architecture-approval"))
    peer_review_service = _FakePeerReviewService(
        report=GovernanceGateReport(status="reviewed", decision="blocked")
    )
    governance_service = _FakeGovernanceService(accepted=False)

    await _enforce_peer_review_gate(
        session_id="session-1",
        requesting_user_id="user-1",
        request_id="req-1",
        workflow_run_id=None,
        approval_service=approval_service,  # type: ignore[arg-type]
        peer_review_service=peer_review_service,  # type: ignore[arg-type]
        governance_service=governance_service,  # type: ignore[arg-type]
    )

    assert peer_review_service.calls == []


async def test_final_output_checkpoint_requires_workflow_run_id() -> None:
    approval_service = _FakeApprovalService(request=_approval_request("final-output-approval"))
    peer_review_service = _FakePeerReviewService(
        report=GovernanceGateReport(status="reviewed", decision="approved")
    )
    governance_service = _FakeGovernanceService(accepted=False)

    with pytest.raises(PeerReviewGateBlockedError):
        await _enforce_peer_review_gate(
            session_id="session-1",
            requesting_user_id="user-1",
            request_id="req-1",
            workflow_run_id=None,
            approval_service=approval_service,  # type: ignore[arg-type]
            peer_review_service=peer_review_service,  # type: ignore[arg-type]
            governance_service=governance_service,  # type: ignore[arg-type]
        )


async def test_final_output_checkpoint_passes_when_all_gates_approved() -> None:
    approval_service = _FakeApprovalService(request=_approval_request("final-output-approval"))
    peer_review_service = _FakePeerReviewService(
        report=GovernanceGateReport(status="reviewed", decision="approved")
    )
    governance_service = _FakeGovernanceService(accepted=False)

    await _enforce_peer_review_gate(
        session_id="session-1",
        requesting_user_id="user-1",
        request_id="req-1",
        workflow_run_id="run-1",
        approval_service=approval_service,  # type: ignore[arg-type]
        peer_review_service=peer_review_service,  # type: ignore[arg-type]
        governance_service=governance_service,  # type: ignore[arg-type]
    )

    assert governance_service.calls == []  # never needed to check risk acceptance


async def test_final_output_checkpoint_blocks_when_decision_blocked_and_no_risk_acceptance() -> None:
    approval_service = _FakeApprovalService(request=_approval_request("final-output-approval"))
    peer_review_service = _FakePeerReviewService(
        report=GovernanceGateReport(status="reviewed", decision="blocked")
    )
    governance_service = _FakeGovernanceService(accepted=False)

    with pytest.raises(PeerReviewGateBlockedError):
        await _enforce_peer_review_gate(
            session_id="session-1",
            requesting_user_id="user-1",
            request_id="req-1",
            workflow_run_id="run-1",
            approval_service=approval_service,  # type: ignore[arg-type]
            peer_review_service=peer_review_service,  # type: ignore[arg-type]
            governance_service=governance_service,  # type: ignore[arg-type]
        )

    assert governance_service.calls == [{"session_id": "session-1", "workflow_run_id": "run-1"}]


async def test_final_output_checkpoint_passes_when_blocked_but_risk_accepted() -> None:
    approval_service = _FakeApprovalService(request=_approval_request("final-output-approval"))
    peer_review_service = _FakePeerReviewService(
        report=GovernanceGateReport(status="reviewed", decision="blocked")
    )
    governance_service = _FakeGovernanceService(accepted=True)

    await _enforce_peer_review_gate(
        session_id="session-1",
        requesting_user_id="user-1",
        request_id="req-1",
        workflow_run_id="run-1",
        approval_service=approval_service,  # type: ignore[arg-type]
        peer_review_service=peer_review_service,  # type: ignore[arg-type]
        governance_service=governance_service,  # type: ignore[arg-type]
    )


async def test_missing_approval_request_is_not_gated() -> None:
    approval_service = _FakeApprovalService(request=None)
    peer_review_service = _FakePeerReviewService(
        report=GovernanceGateReport(status="reviewed", decision="blocked")
    )
    governance_service = _FakeGovernanceService(accepted=False)

    await _enforce_peer_review_gate(
        session_id="session-1",
        requesting_user_id="user-1",
        request_id="req-1",
        workflow_run_id=None,
        approval_service=approval_service,  # type: ignore[arg-type]
        peer_review_service=peer_review_service,  # type: ignore[arg-type]
        governance_service=governance_service,  # type: ignore[arg-type]
    )
