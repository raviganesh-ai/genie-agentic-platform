"""Approval API routes.

Thin wrapper over ``ApprovalService``: lists a session's approval requests
and decides a pending request. The deciding user's identity
(``AuthenticatedUser.user_id``) is always used as ``decided_by`` - never a
client-supplied value - so every decision is attributable to the
authenticated caller.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import get_approval_service, get_session_service
from app.governance.approval_service import ApprovalService
from app.models.approval_models import ApprovalDecision, ApprovalDecisionOutcome, ApprovalRequest
from app.security.auth_models import AuthenticatedUser
from app.security.dependencies import get_current_user
from app.services.session_service import SessionService

router = APIRouter(prefix="/sessions/{session_id}/approvals", tags=["approvals"])


class DecideApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: ApprovalDecisionOutcome
    rationale: str = Field(default="")


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
) -> ApprovalDecision:
    await session_service.get_session(session_id=session_id, requesting_user_id=user.user_id)
    return await approval_service.decide(
        request_id=request_id,
        decision=body.decision,
        decided_by=user.user_id,
        rationale=body.rationale,
    )
