"""Unit tests for EnterpriseKnowledgeStore."""
from __future__ import annotations

import pytest

from app.agents.models import AgentDefinition
from app.memory.enterprise_knowledge_store import EnterpriseKnowledgeStore
from app.memory.memory_access_policy_service import MemoryAccessPolicyService
from app.memory.memory_events import InMemoryMemoryGovernanceRecorder
from app.memory.memory_models import MemoryAccessDeniedError
from app.repositories.enterprise_memory_repository import InMemoryEnterpriseKnowledgeRepository


def _agent(agent_id: str = "agent-a", memory_access: list[str] | None = None) -> AgentDefinition:
    return AgentDefinition(
        id=agent_id,
        name=agent_id,
        role="test_role",
        description="A test agent.",
        memory_access=memory_access if memory_access is not None else ["enterprise"],
    )


def _store(local_settings) -> tuple[EnterpriseKnowledgeStore, InMemoryMemoryGovernanceRecorder]:
    policy_service = MemoryAccessPolicyService.load(local_settings.policies_path)
    recorder = InMemoryMemoryGovernanceRecorder()
    store = EnterpriseKnowledgeStore(
        InMemoryEnterpriseKnowledgeRepository(), policy_service, recorder
    )
    return store, recorder


@pytest.mark.asyncio
async def test_promote_requires_approval(local_settings):
    store, recorder = _store(local_settings)
    with pytest.raises(MemoryAccessDeniedError):
        await store.promote(
            agent=_agent(),
            record_id="pattern-1",
            session_id="session-1",
            trace_id="trace-1",
            classification="industry_pattern",
            content={"summary": "event-driven architecture"},
            approval_status="pending",
        )
    assert recorder.events[-1].event_type == "memory_denied"


@pytest.mark.asyncio
async def test_promote_succeeds_when_approved(local_settings):
    store, recorder = _store(local_settings)
    record = await store.promote(
        agent=_agent(),
        record_id="pattern-1",
        session_id="session-1",
        trace_id="trace-1",
        classification="industry_pattern",
        content={"summary": "event-driven architecture"},
        approval_status="approved",
    )
    assert record.id == "pattern-1"
    assert recorder.events[-1].event_type == "memory_write"


@pytest.mark.asyncio
async def test_promote_denied_without_enterprise_access(local_settings):
    store, recorder = _store(local_settings)
    with pytest.raises(MemoryAccessDeniedError):
        await store.promote(
            agent=_agent(memory_access=["shared"]),
            record_id="pattern-1",
            session_id="session-1",
            trace_id="trace-1",
            classification="industry_pattern",
            content={},
            approval_status="approved",
        )
    assert recorder.events[-1].event_type == "memory_denied"


@pytest.mark.asyncio
async def test_search_requires_reviewer_authorization(local_settings):
    store, recorder = _store(local_settings)
    await store.promote(
        agent=_agent(),
        record_id="pattern-1",
        session_id="session-1",
        trace_id="trace-1",
        classification="industry_pattern",
        content={"summary": "event-driven architecture"},
        approval_status="approved",
    )

    with pytest.raises(MemoryAccessDeniedError):
        await store.search(
            agent=_agent(memory_access=["shared"]),
            query="event",
            session_id="session-1",
            trace_id="trace-2",
        )

    results = await store.search(
        agent=_agent(),
        query="event",
        session_id="session-1",
        trace_id="trace-3",
    )
    assert len(results) == 1
    assert recorder.events[-1].event_type == "memory_read"
