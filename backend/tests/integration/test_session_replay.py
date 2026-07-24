"""Integration test: full session replay reconstruction."""
from __future__ import annotations

import pytest

from app.governance.approval_service import ApprovalPolicyDocument, ApprovalService
from app.governance.decision_graph_service import DecisionGraphService
from app.governance.governance_service import create_governance_service
from app.governance.recommendation_lineage_service import RecommendationLineageService
from app.governance.replay_service import ReplayService
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
async def test_reconstruct_returns_chronologically_ordered_session_state(local_settings):
    governance_service = create_governance_service(settings=local_settings)
    lineage_service = RecommendationLineageService(
        InMemoryRecommendationLineageRepository(), governance_service
    )
    approval_service = _approval_service()
    decision_graph_service = DecisionGraphService()
    replay_service = ReplayService(
        governance_service=governance_service,
        approval_service=approval_service,
        recommendation_lineage_service=lineage_service,
        decision_graph_service=decision_graph_service,
    )

    session_id = "session-1"

    await governance_service.record_agent_registration(
        session_id=session_id, trace_id="trace-1", agent_id="architecture-designer"
    )
    await governance_service.record_execution(
        session_id=session_id, trace_id="trace-1", agent_id="architecture-designer"
    )

    request = await approval_service.request_approval(
        checkpoint_id="architecture-approval",
        session_id=session_id,
        trace_id="trace-1",
        requested_by_agent_id="architecture-designer",
        subject_type="recommendation",
        subject_id="rec-1",
    )
    await approval_service.decide(request_id=request.id, decision="approved", decided_by="reviewer-1")

    await lineage_service.record(
        session_id=session_id,
        trace_id="trace-1",
        recommendation_id="rec-1",
        recommendation_type="architecture_pattern",
        produced_by_agent_id="architecture-designer",
        produced_by_agent_version="1.0.0",
        evidence_references=["doc://1"],
        approval_ids=[request.id],
        confidence_score=0.9,
    )

    decision_graph_service.add_node(
        session_id=session_id, node_id="architecture-designer", node_type="agent", label="Architecture Designer"
    )

    replay = await replay_service.reconstruct(session_id)

    assert replay.session_id == session_id
    # agent_registration + agent_execution + the policy_evaluation event
    # recorded automatically by RecommendationLineageService.record().
    assert len(replay.governance_events) == 3
    assert replay.governance_events == sorted(replay.governance_events, key=lambda e: e.timestamp)
    assert [r.recommendation_id for r in replay.recommendation_lineage] == ["rec-1"]
    assert [r.id for r in replay.approval_requests] == [request.id]
    assert [d.decision for d in replay.approval_decisions] == ["approved"]
    assert [record.event for record in replay.approval_audit_trail] == ["requested", "approved"]
    assert replay.decision_graph is not None
    assert len(replay.decision_graph.nodes) == 1


@pytest.mark.asyncio
async def test_reconstruct_raises_when_session_replay_disabled(local_settings, tmp_path):
    (local_settings.policies_path / "governance_policy.yaml").write_text(
        "agent_execution_governance:\n"
        "  track_registration: true\n"
        "  track_versions: true\n"
        "  track_lifecycle: true\n"
        "  track_executions: true\n"
        "  track_communication: true\n"
        "  track_memory_reads: true\n"
        "  track_memory_writes: true\n"
        "  track_tool_requests: true\n"
        "  track_policy_evaluations: true\n"
        "  track_denied_access: true\n"
        "decision_lineage:\n"
        "  require_evidence_references: true\n"
        "session_replay:\n"
        "  enabled: false\n",
        encoding="utf-8",
    )
    governance_service = create_governance_service(settings=local_settings)
    lineage_service = RecommendationLineageService(
        InMemoryRecommendationLineageRepository(), governance_service
    )
    approval_service = _approval_service()
    decision_graph_service = DecisionGraphService()
    replay_service = ReplayService(
        governance_service=governance_service,
        approval_service=approval_service,
        recommendation_lineage_service=lineage_service,
        decision_graph_service=decision_graph_service,
    )

    with pytest.raises(RuntimeError):
        await replay_service.reconstruct("session-1")
