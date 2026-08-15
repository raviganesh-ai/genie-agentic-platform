"""Session replay & recommendation traceability API routes.

Additive, read-only wrapper over Phase 5's ``ReplayService`` and
``TraceabilityService`` - neither of those services nor
``RecommendationLineageService``/``GovernanceService``/``ApprovalService``/
``DecisionGraphService`` are modified here. No prior phase exposed a REST
endpoint for session replay; this router only adds the thin HTTP surface
Phase 8's Replay Center needs, following the exact same
authenticate -> check session ownership -> delegate-to-service pattern as
every other Phase 7 router.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import (
    get_replay_service,
    get_session_service,
    get_traceability_service,
)
from app.governance.replay_service import ReplayService
from app.governance.traceability_service import TraceabilityService
from app.models.approval_models import ApprovalAuditRecord, ApprovalDecision, ApprovalRequest
from app.models.decision_graph import DecisionGraph
from app.models.governance_event import GovernanceEvent
from app.models.recommendation_lineage import RecommendationLineage
from app.security.auth_models import AuthenticatedUser
from app.security.dependencies import get_current_user
from app.services.session_service import SessionService

router = APIRouter(prefix="/sessions/{session_id}", tags=["replay"])


class SessionReplayResponse(BaseModel):
    """A full, chronologically ordered reconstruction of one session."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1)
    governance_events: list[GovernanceEvent] = Field(default_factory=list)
    recommendation_lineage: list[RecommendationLineage] = Field(default_factory=list)
    approval_requests: list[ApprovalRequest] = Field(default_factory=list)
    approval_decisions: list[ApprovalDecision] = Field(default_factory=list)
    approval_audit_trail: list[ApprovalAuditRecord] = Field(default_factory=list)
    decision_graph: DecisionGraph | None = None


class RecommendationTraceResponse(BaseModel):
    """A single recommendation's full, customer-facing traceability view."""

    model_config = ConfigDict(extra="forbid")

    lineage: RecommendationLineage
    approval_requests: list[ApprovalRequest] = Field(default_factory=list)
    approval_decisions: list[ApprovalDecision] = Field(default_factory=list)
    governance_events: list[GovernanceEvent] = Field(default_factory=list)


@router.get("/replay")
async def get_session_replay(
    session_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
    replay_service: ReplayService = Depends(get_replay_service),
) -> SessionReplayResponse:
    await session_service.get_session(session_id=session_id, requesting_user_id=user.user_id)
    replay = await replay_service.reconstruct(session_id)
    return SessionReplayResponse(
        session_id=replay.session_id,
        governance_events=replay.governance_events,
        recommendation_lineage=replay.recommendation_lineage,
        approval_requests=replay.approval_requests,
        approval_decisions=replay.approval_decisions,
        approval_audit_trail=replay.approval_audit_trail,
        decision_graph=replay.decision_graph,
    )


@router.get("/recommendations/{recommendation_id}/trace")
async def get_recommendation_trace(
    session_id: str,
    recommendation_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
    traceability_service: TraceabilityService = Depends(get_traceability_service),
) -> RecommendationTraceResponse:
    await session_service.get_session(session_id=session_id, requesting_user_id=user.user_id)
    trace = await traceability_service.trace_recommendation(
        session_id=session_id, recommendation_id=recommendation_id
    )
    return RecommendationTraceResponse(
        lineage=trace.lineage,
        approval_requests=trace.approval_requests,
        approval_decisions=trace.approval_decisions,
        governance_events=trace.governance_events,
    )
