"""Unit tests for RecommendationLineageService."""
from __future__ import annotations

import pytest

from app.governance.governance_service import create_governance_service
from app.governance.recommendation_lineage_service import (
    RecommendationLineageError,
    RecommendationLineageService,
)
from app.repositories.recommendation_lineage_repository import (
    InMemoryRecommendationLineageRepository,
)


@pytest.mark.asyncio
async def test_record_and_get_lineage(local_settings):
    service = RecommendationLineageService(InMemoryRecommendationLineageRepository())

    lineage = await service.record(
        session_id="session-1",
        trace_id="trace-1",
        recommendation_id="rec-1",
        recommendation_type="architecture_pattern",
        produced_by_agent_id="architecture-designer",
        produced_by_agent_version="1.0.0",
        evidence_references=["transcript://session-1/segment-2"],
        memory_references=["shared:goal-1"],
        confidence_score=0.9,
    )

    fetched = await service.get(session_id="session-1", recommendation_id="rec-1")
    assert fetched == lineage
    assert fetched.produced_by_agent_id == "architecture-designer"
    assert fetched.evidence_references == ["transcript://session-1/segment-2"]
    assert fetched.memory_references == ["shared:goal-1"]


@pytest.mark.asyncio
async def test_list_for_session_returns_all_recommendations(local_settings):
    service = RecommendationLineageService(InMemoryRecommendationLineageRepository())

    await service.record(
        session_id="session-1",
        trace_id="trace-1",
        recommendation_id="rec-1",
        recommendation_type="architecture_pattern",
        produced_by_agent_id="agent-a",
        produced_by_agent_version="1.0.0",
        evidence_references=["doc://1"],
        confidence_score=0.8,
    )
    await service.record(
        session_id="session-1",
        trace_id="trace-2",
        recommendation_id="rec-2",
        recommendation_type="risk_mitigation",
        produced_by_agent_id="agent-b",
        produced_by_agent_version="2.0.0",
        evidence_references=["doc://2"],
        confidence_score=0.7,
    )

    results = await service.list_for_session(session_id="session-1")
    assert {record.recommendation_id for record in results} == {"rec-1", "rec-2"}


@pytest.mark.asyncio
async def test_missing_evidence_denied_when_policy_requires_it(local_settings):
    governance_service = await _governance_service(local_settings)
    service = RecommendationLineageService(
        InMemoryRecommendationLineageRepository(), governance_service
    )

    with pytest.raises(RecommendationLineageError):
        await service.record(
            session_id="session-1",
            trace_id="trace-1",
            recommendation_id="rec-1",
            recommendation_type="architecture_pattern",
            produced_by_agent_id="agent-a",
            produced_by_agent_version="1.0.0",
            evidence_references=[],
            confidence_score=0.5,
        )


@pytest.mark.asyncio
async def test_lineage_with_evidence_emits_governance_event(local_settings):
    governance_service = await _governance_service(local_settings)
    service = RecommendationLineageService(
        InMemoryRecommendationLineageRepository(), governance_service
    )

    await service.record(
        session_id="session-1",
        trace_id="trace-1",
        recommendation_id="rec-1",
        recommendation_type="architecture_pattern",
        produced_by_agent_id="agent-a",
        produced_by_agent_version="1.0.0",
        evidence_references=["doc://1"],
        confidence_score=0.5,
    )

    events = await governance_service.events_for_session("session-1")
    assert any(event.category == "policy_evaluation" for event in events)


async def _governance_service(settings):
    return create_governance_service(settings=settings)
