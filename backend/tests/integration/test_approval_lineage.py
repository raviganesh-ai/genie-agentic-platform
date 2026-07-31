"""Integration test: approval audit trail correlates with recommendation lineage."""
from __future__ import annotations

import pytest

from app.governance.approval_service import ApprovalPolicyDocument, ApprovalService
from app.governance.governance_service import create_governance_service
from app.governance.recommendation_lineage_service import RecommendationLineageService
from app.models.approval_models import ApprovalCheckpoint
from app.repositories.approval_repository import InMemoryApprovalRepository
from app.repositories.recommendation_lineage_repository import (
    InMemoryRecommendationLineageRepository,
)


@pytest.mark.asyncio
async def test_approval_audit_trace_id_matches_recommendation_trace_id(local_settings):
    governance_service = create_governance_service(settings=local_settings)
    lineage_service = RecommendationLineageService(
        InMemoryRecommendationLineageRepository(), governance_service
    )
    policy = ApprovalPolicyDocument(
        default_expiry_minutes=1440,
        checkpoints=[
            ApprovalCheckpoint(
                id="final-output-approval",
                name="Final Output Approval",
                description="Approve the final output before delivery.",
            )
        ],
    )
    approval_service = ApprovalService(
        repository=InMemoryApprovalRepository(),
        policy=policy,
        governance_service=governance_service,
    )

    request = await approval_service.request_approval(
        checkpoint_id="final-output-approval",
        session_id="session-1",
        trace_id="trace-42",
        requested_by_agent_id="peer-review-agent",
        subject_type="recommendation",
        subject_id="rec-1",
    )
    await approval_service.decide(request_id=request.id, decision="approved", decided_by="reviewer-1")

    lineage = await lineage_service.record(
        session_id="session-1",
        trace_id="trace-42",
        recommendation_id="rec-1",
        recommendation_type="final_output",
        produced_by_agent_id="peer-review-agent",
        produced_by_agent_version="1.0.0",
        evidence_references=["doc://1"],
        approval_ids=[request.id],
        confidence_score=1.0,
    )

    audit_trail = await approval_service.audit_trail(session_id="session-1")
    assert all(record.trace_id == lineage.trace_id for record in audit_trail)
    assert lineage.approval_ids == [request.id]

    # The governance service must have also recorded the approval audit
    # events (dual-write), so external providers/replay can see them too.
    events_from_provider = governance_service._provider.approval_audit_records  # type: ignore[attr-defined]
    assert len(events_from_provider) == 2
    assert {record.event for record in events_from_provider} == {"requested", "approved"}
