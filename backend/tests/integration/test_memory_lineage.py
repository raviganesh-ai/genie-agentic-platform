"""Verifies every memory write records complete decision lineage."""
from __future__ import annotations

import pytest

from app.agents.models import AgentDefinition
from app.memory.memory_service import create_memory_service


def _agent(agent_id: str = "agent-a") -> AgentDefinition:
    return AgentDefinition(
        id=agent_id,
        name=agent_id,
        role="test_role",
        description="A test agent.",
        memory_access=["personal", "shared", "enterprise"],
        version="3.2.1",
    )


@pytest.mark.asyncio
async def test_personal_write_lineage_has_all_required_fields(local_settings):
    service = create_memory_service(settings=local_settings)
    agent = _agent()

    record = await service.personal.write(
        agent=agent,
        session_id="session-1",
        trace_id="trace-1",
        classification="reasoning_summary",
        content={"summary": "..."},
        evidence_references=["transcript://session-1/segment-4"],
        confidence_score=0.87,
    )

    lineage = record.lineage
    assert lineage.session_id == "session-1"
    assert lineage.trace_id == "trace-1"
    assert lineage.agent_id == agent.id
    assert lineage.agent_version == "3.2.1"
    assert lineage.timestamp is not None
    assert lineage.evidence_references == ["transcript://session-1/segment-4"]
    assert lineage.confidence_score == 0.87
    assert lineage.approval_status == "not_required"
    assert record.classification == "reasoning_summary"


@pytest.mark.asyncio
async def test_shared_write_lineage_has_all_required_fields(local_settings):
    service = create_memory_service(settings=local_settings)
    agent = _agent()

    record = await service.shared.write(
        agent=agent,
        session_id="session-1",
        trace_id="trace-2",
        key="risk-1",
        classification="risk",
        content={"text": "vendor lock-in"},
        approval_status="approved",
        evidence_references=["doc://risk-register#1"],
        confidence_score=0.65,
    )

    lineage = record.lineage
    assert lineage.session_id == "session-1"
    assert lineage.trace_id == "trace-2"
    assert lineage.agent_id == agent.id
    assert lineage.agent_version == "3.2.1"
    assert lineage.timestamp is not None
    assert lineage.evidence_references == ["doc://risk-register#1"]
    assert lineage.confidence_score == 0.65
    assert lineage.approval_status == "approved"
    assert record.classification == "risk"


@pytest.mark.asyncio
async def test_enterprise_promote_lineage_has_all_required_fields(local_settings):
    service = create_memory_service(settings=local_settings)
    agent = _agent()

    record = await service.enterprise.promote(
        agent=agent,
        record_id="pattern-1",
        session_id="session-1",
        trace_id="trace-3",
        classification="reference_architecture",
        content={"summary": "event-driven ingestion pipeline"},
        approval_status="approved",
        evidence_references=["architecture://session-1/diagram-2"],
        confidence_score=0.92,
    )

    lineage = record.lineage
    assert lineage.session_id == "session-1"
    assert lineage.trace_id == "trace-3"
    assert lineage.agent_id == agent.id
    assert lineage.agent_version == "3.2.1"
    assert lineage.timestamp is not None
    assert lineage.evidence_references == ["architecture://session-1/diagram-2"]
    assert lineage.confidence_score == 0.92
    assert lineage.approval_status == "approved"
    assert record.classification == "reference_architecture"
