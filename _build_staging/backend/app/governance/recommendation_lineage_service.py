"""Recommendation lineage service.

Records and queries ``RecommendationLineage`` (see "LINEAGE" in the Phase 5
instructions): which agent produced a recommendation, which evidence was
used, which memory entries contributed, and which approvals were granted.
Optionally emits a ``policy_evaluation`` governance event for every
recorded lineage so recommendation creation is itself part of the audit
trail queried by ``TraceabilityService``/``ReplayService``.
"""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.governance.governance_service import GovernanceService
from app.models.recommendation_lineage import RecommendationLineage
from app.repositories.recommendation_lineage_repository import RecommendationLineageRepository

__all__ = ["RecommendationLineageError", "RecommendationLineageService"]


class RecommendationLineageError(RuntimeError):
    """Raised when a recommendation lineage record violates governance policy."""


class RecommendationLineageService:
    """Records full provenance for every recommendation an agent produces."""

    def __init__(
        self,
        repository: RecommendationLineageRepository,
        governance_service: GovernanceService | None = None,
    ) -> None:
        self._repository = repository
        self._governance_service = governance_service

    async def record(
        self,
        *,
        session_id: str,
        trace_id: str,
        recommendation_id: str,
        recommendation_type: str,
        produced_by_agent_id: str,
        produced_by_agent_version: str,
        evidence_references: list[str] | None = None,
        memory_references: list[str] | None = None,
        approval_ids: list[str] | None = None,
        confidence_score: float = 1.0,
    ) -> RecommendationLineage:
        resolved_evidence = evidence_references or []

        if (
            self._governance_service is not None
            and self._governance_service.policy.decision_lineage.require_evidence_references
            and not resolved_evidence
        ):
            raise RecommendationLineageError(
                f"Recommendation '{recommendation_id}' has no evidence_references, "
                f"but governance policy decision_lineage.require_evidence_references "
                f"is enabled."
            )

        lineage = RecommendationLineage(
            id=str(uuid4()),
            session_id=session_id,
            trace_id=trace_id,
            recommendation_id=recommendation_id,
            recommendation_type=recommendation_type,
            produced_by_agent_id=produced_by_agent_id,
            produced_by_agent_version=produced_by_agent_version,
            evidence_references=resolved_evidence,
            memory_references=memory_references or [],
            approval_ids=approval_ids or [],
            confidence_score=confidence_score,
            timestamp=datetime.now(UTC),
        )
        await self._repository.put(lineage)

        if self._governance_service is not None:
            await self._governance_service.record_policy_evaluation(
                session_id=session_id,
                trace_id=trace_id,
                agent_id=produced_by_agent_id,
                policy_name="decision_lineage.require_evidence_references",
                allowed=True,
                detail={
                    "recommendation_id": recommendation_id,
                    "evidence_count": len(lineage.evidence_references),
                    "memory_reference_count": len(lineage.memory_references),
                },
            )
        return lineage

    async def get(
        self, *, session_id: str, recommendation_id: str
    ) -> RecommendationLineage | None:
        return await self._repository.get(session_id=session_id, recommendation_id=recommendation_id)

    async def list_for_session(self, *, session_id: str) -> list[RecommendationLineage]:
        return await self._repository.list_for_session(session_id=session_id)
