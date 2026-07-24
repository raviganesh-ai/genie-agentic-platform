"""Unit tests for PersonalMemoryStore."""
from __future__ import annotations

import pytest

from app.agents.models import AgentDefinition
from app.memory.memory_access_policy_service import MemoryAccessPolicyService
from app.memory.memory_events import InMemoryMemoryGovernanceRecorder
from app.memory.memory_models import MemoryAccessDeniedError
from app.memory.personal_memory_store import PersonalMemoryStore
from app.repositories.personal_memory_repository import InMemoryPersonalMemoryRepository


def _agent(agent_id: str, memory_access: list[str] | None = None) -> AgentDefinition:
    return AgentDefinition(
        id=agent_id,
        name=agent_id,
        role="test_role",
        description="A test agent.",
        memory_access=memory_access if memory_access is not None else ["personal"],
        version="2.3.1",
    )


def _store(local_settings) -> tuple[PersonalMemoryStore, InMemoryMemoryGovernanceRecorder]:
    policy_service = MemoryAccessPolicyService.load(local_settings.policies_path)
    recorder = InMemoryMemoryGovernanceRecorder()
    store = PersonalMemoryStore(InMemoryPersonalMemoryRepository(), policy_service, recorder)
    return store, recorder


@pytest.mark.asyncio
async def test_write_then_read_by_owning_agent(local_settings):
    store, recorder = _store(local_settings)
    agent = _agent("agent-a")

    record = await store.write(
        agent=agent,
        session_id="session-1",
        trace_id="trace-1",
        classification="observation",
        content={"note": "hello"},
    )
    assert record.agent_id == "agent-a"
    assert record.lineage.agent_version == "2.3.1"

    results = await store.read(
        requesting_agent=agent, owning_agent_id="agent-a", session_id="session-1", trace_id="trace-2"
    )
    assert len(results) == 1
    assert results[0].content == {"note": "hello"}

    event_types = [event.event_type for event in recorder.events]
    assert "memory_write" in event_types
    assert "memory_read" in event_types


@pytest.mark.asyncio
async def test_write_denied_when_agent_lacks_personal_access(local_settings):
    store, recorder = _store(local_settings)
    agent = _agent("agent-a", memory_access=["shared"])

    with pytest.raises(MemoryAccessDeniedError):
        await store.write(
            agent=agent,
            session_id="session-1",
            trace_id="trace-1",
            classification="observation",
            content={},
        )
    assert recorder.events[-1].event_type == "memory_denied"


@pytest.mark.asyncio
async def test_read_denied_for_other_agent(local_settings):
    store, recorder = _store(local_settings)
    owner = _agent("agent-a")
    other = _agent("agent-b")

    await store.write(
        agent=owner,
        session_id="session-1",
        trace_id="trace-1",
        classification="observation",
        content={"note": "secret"},
    )

    with pytest.raises(MemoryAccessDeniedError):
        await store.read(
            requesting_agent=other,
            owning_agent_id="agent-a",
            session_id="session-1",
            trace_id="trace-2",
        )
    assert recorder.events[-1].event_type == "memory_denied"
