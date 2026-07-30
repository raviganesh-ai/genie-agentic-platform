"""Governance API routes.

Thin, read-only wrapper over ``GovernanceService.events_for_session`` - the
full governance/audit trail (agent registration, executions, communication,
memory reads/writes, tool requests, policy evaluations, denied access) for
one session, per the Governance Requirements in
``.github/copilot-instructions.md``. Also exposes write endpoints:
``POST /checkpoints/confirm`` (the Discovery Wizard's Responsible AI
Accountability "Proceed to Next Step" gate), ``GET .../gate-report`` (the
Governance Reviewer's Peer Review verdict, see
``app.services.peer_review_service``), ``GET .../agent-assessments`` (the
Security Assessment Agent's and Test Generation Agent's own early, per-
agent gate verdicts - available before the consolidated gate report),
``GET .../service-policy`` (the real, consolidated policy - deployment
approval checkpoints, governance tracking, memory access policy, and the
Governance Reviewer's own access-control narrative - that will govern this
build once deployed, see ``app.services.service_policy_service``),
``POST .../fixes`` (regenerate the build to resolve selected findings and
re-run every gate step), and ``POST .../risk-acceptance`` (a human's
explicit, justified acceptance of residual Peer Review risk before
deploying anyway).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import (
    get_governance_service,
    get_peer_review_service,
    get_service_policy_service,
    get_session_service,
)
from app.governance.governance_service import GovernanceService
from app.models.governance_event import GovernanceEvent
from app.models.governance_gate_report import AgentAssessmentsReport, GovernanceGateReport
from app.models.service_policy import ServicePolicy
from app.models.workflow_models import WorkflowRunResult
from app.security.auth_models import AuthenticatedUser
from app.security.dependencies import get_current_user
from app.services.peer_review_service import PeerReviewService
from app.services.service_policy_service import ServicePolicyService
from app.services.session_service import SessionService

router = APIRouter(prefix="/sessions/{session_id}/governance", tags=["governance"])


class ConfirmCheckpointRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str = Field(min_length=1)
    stage_key: str = Field(min_length=1)
    stage_label: str = Field(min_length=1)


class ApplyFixesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str | None = Field(default=None)
    selected_findings: list[str] = Field(default_factory=list)


class RiskAcceptanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str = Field(min_length=1)
    justification: str = Field(min_length=1)
    accepted_finding_ids: list[str] = Field(default_factory=list)


@router.get("/events")
async def list_governance_events(
    session_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
    governance_service: GovernanceService = Depends(get_governance_service),
) -> list[GovernanceEvent]:
    await session_service.get_session(session_id=session_id, requesting_user_id=user.user_id)
    return await governance_service.events_for_session(session_id)


@router.post("/checkpoints/confirm")
async def confirm_checkpoint(
    session_id: str,
    body: ConfirmCheckpointRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
    governance_service: GovernanceService = Depends(get_governance_service),
) -> GovernanceEvent:
    await session_service.get_session(session_id=session_id, requesting_user_id=user.user_id)
    return await governance_service.record_human_checkpoint_confirmation(
        session_id=session_id,
        trace_id=body.trace_id,
        stage_key=body.stage_key,
        stage_label=body.stage_label,
        confirmed_by=user.user_id,
    )


@router.get("/{workflow_run_id}/gate-report")
async def get_gate_report(
    session_id: str,
    workflow_run_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    peer_review_service: PeerReviewService = Depends(get_peer_review_service),
) -> GovernanceGateReport:
    return await peer_review_service.get_gate_report(
        session_id=session_id, requesting_user_id=user.user_id, workflow_run_id=workflow_run_id
    )


@router.get("/{workflow_run_id}/agent-assessments")
async def get_agent_assessments(
    session_id: str,
    workflow_run_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    peer_review_service: PeerReviewService = Depends(get_peer_review_service),
) -> AgentAssessmentsReport:
    """Early-visibility per-agent verdicts (Security Assessment Agent's own
    security gate, Test Generation Agent's own test-coverage gate) - each
    available as soon as that agent's own step completes, without waiting
    for the slower, consolidated Governance Reviewer verdict at
    ``/gate-report``.
    """
    return await peer_review_service.get_agent_assessments(
        session_id=session_id, requesting_user_id=user.user_id, workflow_run_id=workflow_run_id
    )


@router.get("/{workflow_run_id}/service-policy")
async def get_service_policy(
    session_id: str,
    workflow_run_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service_policy_service: ServicePolicyService = Depends(get_service_policy_service),
) -> ServicePolicy:
    """The real, consolidated policy that will govern this build once deployed:
    the approval checkpoints gating deployment (with their actual per-session
    status), the governance tracking/decision-lineage/session-replay policy,
    the memory tier access policy, and the Governance Reviewer agent's own
    real access-control narrative for this specific build.
    """
    return await service_policy_service.get_service_policy(
        session_id=session_id, requesting_user_id=user.user_id, workflow_run_id=workflow_run_id
    )


@router.post("/{workflow_run_id}/fixes")
async def apply_selected_fixes(
    session_id: str,
    workflow_run_id: str,
    body: ApplyFixesRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    peer_review_service: PeerReviewService = Depends(get_peer_review_service),
) -> WorkflowRunResult:
    return await peer_review_service.apply_selected_fixes(
        session_id=session_id,
        requesting_user_id=user.user_id,
        workflow_run_id=workflow_run_id,
        selected_findings=body.selected_findings,
        trace_id=body.trace_id,
    )


@router.post("/{workflow_run_id}/risk-acceptance")
async def accept_risk(
    session_id: str,
    workflow_run_id: str,
    body: RiskAcceptanceRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
    governance_service: GovernanceService = Depends(get_governance_service),
) -> GovernanceEvent:
    await session_service.get_session(session_id=session_id, requesting_user_id=user.user_id)
    return await governance_service.record_risk_acceptance(
        session_id=session_id,
        trace_id=body.trace_id,
        workflow_run_id=workflow_run_id,
        justification=body.justification,
        accepted_finding_ids=body.accepted_finding_ids,
        accepted_by=user.user_id,
    )
