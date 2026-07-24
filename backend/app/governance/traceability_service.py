"""Traceability service.

Assembles the consolidated view a customer needs to answer: which agent
created a recommendation, which evidence was used, which memory entries
contributed, and which approvals were granted (see "LINEAGE" in the Phase
5 instructions). Composes ``RecommendationLineageService``,
``ApprovalService``, and ``GovernanceService`` rather than duplicating
their storage.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.governance.approval_service import ApprovalService
from app.governance.governance_service import GovernanceService
from app.governance.recommendation_lineage_service import RecommendationLineageService
from app.models.approval_models import ApprovalDecision, ApprovalRequest
from app.models.governance_event import GovernanceEvent
from app.models.recommendation_lineage import RecommendationLineage

__all__ = ["RecommendationTrace", "TraceabilityService", "UnknownRecommendationError"]


class UnknownRecommendationError(RuntimeError):
    """Raised when tracing a recommendation with no recorded lineage."""


@dataclass(frozen=True)
class RecommendationTrace:
    """A single recommendation's full, customer-facing traceability view."""

    lineage: RecommendationLineage
    approval_requests: list[ApprovalRequest] = field(default_factory=list)
    approval_decisions: list[ApprovalDecision] = field(default_factory=list)
    governance_events: list[GovernanceEvent] = field(default_factory=list)


class TraceabilityService:
    """Assembles decision/recommendation lineage across governance, memory, and approvals."""

    def __init__(
        self,
        *,
        recommendation_lineage_service: RecommendationLineageService,
        approval_service: ApprovalService,
        governance_service: GovernanceService,
    ) -> None:
        self._recommendation_lineage_service = recommendation_lineage_service
        self._approval_service = approval_service
        self._governance_service = governance_service

    async def trace_recommendation(
        self, *, session_id: str, recommendation_id: str
    ) -> RecommendationTrace:
        lineage = await self._recommendation_lineage_service.get(
            session_id=session_id, recommendation_id=recommendation_id
        )
        if lineage is None:
            raise UnknownRecommendationError(
                f"No recommendation lineage recorded for recommendation "
                f"'{recommendation_id}' in session '{session_id}'."
            )

        all_requests = await self._approval_service.list_requests_for_session(session_id)
        approval_requests = [
            request for request in all_requests if request.id in lineage.approval_ids
        ]

        all_decisions = await self._approval_service.list_decisions_for_session(session_id)
        request_ids = {request.id for request in approval_requests}
        approval_decisions = [
            decision for decision in all_decisions if decision.request_id in request_ids
        ]

        session_events = await self._governance_service.events_for_session(session_id)
        governance_events = [
            event for event in session_events if event.trace_id == lineage.trace_id
        ]

        return RecommendationTrace(
            lineage=lineage,
            approval_requests=approval_requests,
            approval_decisions=approval_decisions,
            governance_events=governance_events,
        )
