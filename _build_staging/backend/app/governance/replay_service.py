"""Session replay service.

Implements "SESSION REPLAY" for Phase 5: reconstructs a session's workflow
execution sequence, agent execution sequence, memory changes, approvals,
and governance events into one chronologically ordered view, so the
Mission Control UI (and auditors) can play back exactly what happened.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.governance.approval_service import ApprovalService
from app.governance.decision_graph_service import DecisionGraphService
from app.governance.governance_service import GovernanceService
from app.governance.recommendation_lineage_service import RecommendationLineageService
from app.models.approval_models import ApprovalAuditRecord, ApprovalDecision, ApprovalRequest
from app.models.decision_graph import DecisionGraph
from app.models.governance_event import GovernanceEvent
from app.models.recommendation_lineage import RecommendationLineage

__all__ = ["ReplayService", "SessionReplay"]


@dataclass(frozen=True)
class SessionReplay:
    """A full, chronologically ordered reconstruction of one session."""

    session_id: str
    governance_events: list[GovernanceEvent] = field(default_factory=list)
    recommendation_lineage: list[RecommendationLineage] = field(default_factory=list)
    approval_requests: list[ApprovalRequest] = field(default_factory=list)
    approval_decisions: list[ApprovalDecision] = field(default_factory=list)
    approval_audit_trail: list[ApprovalAuditRecord] = field(default_factory=list)
    decision_graph: DecisionGraph | None = None


class ReplayService:
    """Reconstructs a full ``SessionReplay`` from governance, lineage, and approval state."""

    def __init__(
        self,
        *,
        governance_service: GovernanceService,
        approval_service: ApprovalService,
        recommendation_lineage_service: RecommendationLineageService,
        decision_graph_service: DecisionGraphService,
    ) -> None:
        self._governance_service = governance_service
        self._approval_service = approval_service
        self._recommendation_lineage_service = recommendation_lineage_service
        self._decision_graph_service = decision_graph_service

    async def reconstruct(self, session_id: str) -> SessionReplay:
        if not self._governance_service.policy.session_replay.enabled:
            raise RuntimeError(
                "Session replay is disabled by governance policy "
                "(session_replay.enabled=false)."
            )

        events = sorted(
            await self._governance_service.events_for_session(session_id),
            key=lambda event: event.timestamp,
        )
        lineage = sorted(
            await self._recommendation_lineage_service.list_for_session(session_id=session_id),
            key=lambda record: record.timestamp,
        )
        requests = sorted(
            await self._approval_service.list_requests_for_session(session_id),
            key=lambda request: request.requested_at,
        )
        decisions = sorted(
            await self._approval_service.list_decisions_for_session(session_id),
            key=lambda decision: decision.decided_at,
        )
        audit_trail = sorted(
            await self._approval_service.audit_trail(session_id=session_id),
            key=lambda record: record.timestamp,
        )
        graph = self._decision_graph_service.get_graph(session_id)

        return SessionReplay(
            session_id=session_id,
            governance_events=events,
            recommendation_lineage=lineage,
            approval_requests=requests,
            approval_decisions=decisions,
            approval_audit_trail=audit_trail,
            decision_graph=graph,
        )
