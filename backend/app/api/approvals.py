"""Approval API routes.

Thin wrapper over ``ApprovalService``: lists a session's approval requests
and decides a pending request. The deciding user's identity
(``AuthenticatedUser.user_id``) is always used as ``decided_by`` - never a
client-supplied value - so every decision is attributable to the
authenticated caller.

Approving the ``final-output-approval`` checkpoint (the deploy gate) is
additionally hardened: it fails closed unless the Peer Review Agent's
Peer Review verdict passed every gate, or a risk acceptance has already
been recorded for the named ``workflow_run_id`` (see
``app.services.peer_review_service`` / ``GovernanceService.record_risk_acceptance``).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import (
    get_approval_service,
    get_governance_service,
    get_peer_review_service,
    get_session_service,
)
from app.governance.approval_service import ApprovalService
from app.governance.governance_service import GovernanceService
from app.models.approval_models import ApprovalDecision, ApprovalDecisionOutcome, ApprovalRequest
from app.security.auth_models import AuthenticatedUser
from app.security.dependencies import get_current_user
from app.services.peer_review_service import PeerReviewGateBlockedError, PeerReviewService
from app.services.session_service import SessionService

router = APIRouter(prefix="/sessions/{session_id}/approvals", tags=["approvals"])

_FINAL_OUTPUT_CHECKPOINT_ID = "final-output-approval"


class DecideApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: ApprovalDecisionOutcome
    rationale: str = Field(default="")
    workflow_run_id: str | None = Field(
        default=None,
        description=(
            "Required when approving the final-output-approval checkpoint, "
            "so the Peer Review gate report for that specific run can be "
            "checked before the deploy gate is allowed to open."
        ),
    )


@router.get("")
async def list_approvals(
    session_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
    approval_service: ApprovalService = Depends(get_approval_service),
) -> list[ApprovalRequest]:
    await session_service.get_session(session_id=session_id, requesting_user_id=user.user_id)
    return await approval_service.list_requests_for_session(session_id)


@router.post("/{request_id}/decide")
async def decide_approval(
    session_id: str,
    request_id: str,
    body: DecideApprovalRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
    approval_service: ApprovalService = Depends(get_approval_service),
    peer_review_service: PeerReviewService = Depends(get_peer_review_service),
    governance_service: GovernanceService = Depends(get_governance_service),
) -> ApprovalDecision:
    await session_service.get_session(session_id=session_id, requesting_user_id=user.user_id)

    if body.decision == "approved":
        await _enforce_peer_review_gate(
            session_id=session_id,
            requesting_user_id=user.user_id,
            request_id=request_id,
            workflow_run_id=body.workflow_run_id,
            approval_service=approval_service,
            peer_review_service=peer_review_service,
            governance_service=governance_service,
        )

    return await approval_service.decide(
        request_id=request_id,
        decision=body.decision,
        decided_by=user.user_id,
        rationale=body.rationale,
    )


async def _enforce_peer_review_gate(
    *,
    session_id: str,
    requesting_user_id: str,
    request_id: str,
    workflow_run_id: str | None,
    approval_service: ApprovalService,
    peer_review_service: PeerReviewService,
    governance_service: GovernanceService,
) -> None:
    pending_request = await approval_service.get_request(request_id)
    if pending_request is None or pending_request.checkpoint_id != _FINAL_OUTPUT_CHECKPOINT_ID:
        return

    if not workflow_run_id:
        raise PeerReviewGateBlockedError(
            "workflow_run_id is required to approve the final output checkpoint."
        )

    gate_report = await peer_review_service.get_gate_report(
        session_id=session_id, requesting_user_id=requesting_user_id, workflow_run_id=workflow_run_id
    )
    if gate_report.decision != "blocked":
        return

    accepted = await governance_service.has_risk_acceptance(
        session_id=session_id, workflow_run_id=workflow_run_id
    )
    if not accepted:
        raise PeerReviewGateBlockedError(
            "Peer Review has blocked this deployment and no risk acceptance has been "
            "recorded for this workflow run. Apply fixes for the open findings, or "
            "explicitly accept the risk with a justification, before approving deploy."
        )
