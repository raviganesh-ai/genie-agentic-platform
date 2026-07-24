"""Integration test: end-to-end recommendation traceability."""
from __future__ import annotations

import pytest

from app.governance.approval_service import ApprovalPolicyDocument, ApprovalService
from app.governance.governance_service import create_governance_service
from app.governance.recommendation_lineage_service import RecommendationLineageService
from app.governance.traceability_service import TraceabilityService, UnknownRecommendationError
from app.models.approval_models import ApprovalCheckpoint
from app.repositories.approval_repository import InMemoryApprovalRepository
from app.repositories.recommendation_lineage_repository import (
    InMemoryRecommendationLineageRepository,
)


def _approval_service() -> ApprovalService:
    policy = ApprovalPolicyDocument(
        default_expiry_minutes=1440,
        checkpoints=[
            ApprovalCheckpoint(
                id="architecture-approval",
                name="Architecture Approval",
                description="Approve the proposed architecture.",
            )
        ],
    )
    return ApprovalService(repository=InMemoryApprovalRepository(), policy=policy)


@pytest.mark.asyncio
async def test_trace_recommendation_assembles_lineage_approvals_and_events(local_settings):
    governance_service = create_governance_service(settings=local_settings)
    lineage_service = RecommendationLineageService(
        InMemoryRecommendationLineageRepository(), governance_service
    )
    approval_service = _approval_service()
    traceability_service = TraceabilityService(
        recommendation_lineage_service=lineage_service,
        approval_service=approval_service,
        governance_service=governance_service,
    )

    await governance_service.record_execution(
        session_id="session-1", trace_id="trace-1", agent_id="architecture-designer"
    )

    approval_request = await approval_service.request_approval(
        checkpoint_id="architecture-approval",
        session_id="session-1",
        trace_id="trace-1",
        requested_by_agent_id="architecture-designer",
        subject_type="recommendation",
        subject_id="rec-1",
    )
    await approval_service.decide(
        request_id=approval_request.id, decision="approved", decided_by="reviewer-1"
    )

    await lineage_service.record(
        session_id="session-1",
        trace_id="trace-1",
        recommendation_id="rec-1",
        recommendation_type="architecture_pattern",
        produced_by_agent_id="architecture-designer",
        produced_by_agent_version="1.0.0",
        evidence_references=["transcript://session-1/segment-4"],
        approval_ids=[approval_request.id],
        confidence_score=0.95,
    )

    trace = await traceability_service.trace_recommendation(
        session_id="session-1", recommendation_id="rec-1"
    )

    assert trace.lineage.produced_by_agent_id == "architecture-designer"
    assert trace.lineage.evidence_references == ["transcript://session-1/segment-4"]
    assert [request.id for request in trace.approval_requests] == [approval_request.id]
    assert [decision.decision for decision in trace.approval_decisions] == ["approved"]
    assert any(event.category == "agent_execution" for event in trace.governance_events)


@pytest.mark.asyncio
async def test_trace_unknown_recommendation_raises(local_settings):
    governance_service = create_governance_service(settings=local_settings)
    lineage_service = RecommendationLineageService(
        InMemoryRecommendationLineageRepository(), governance_service
    )
    approval_service = _approval_service()
    traceability_service = TraceabilityService(
        recommendation_lineage_service=lineage_service,
        approval_service=approval_service,
        governance_service=governance_service,
    )

    with pytest.raises(UnknownRecommendationError):
        await traceability_service.trace_recommendation(
            session_id="session-1", recommendation_id="does-not-exist"
        )
