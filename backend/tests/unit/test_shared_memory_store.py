"""Unit tests for SharedMemoryStore."""
from __future__ import annotations

import pytest

from app.agents.models import AgentDefinition
from app.memory.memory_access_policy_service import MemoryAccessPolicyService
from app.memory.memory_events import InMemoryMemoryGovernanceRecorder
from app.memory.memory_models import MemoryAccessDeniedError
from app.memory.shared_memory_store import SharedMemoryStore
from app.repositories.shared_memory_repository import InMemorySharedMemoryRepository


def _agent(agent_id: str = "agent-a", memory_access: list[str] | None = None) -> AgentDefinition:
    return AgentDefinition(
        id=agent_id,
        name=agent_id,
        role="test_role",
        description="A test agent.",
        memory_access=memory_access if memory_access is not None else ["shared"],
    )


def _store(local_settings) -> tuple[SharedMemoryStore, InMemoryMemoryGovernanceRecorder]:
    policy_service = MemoryAccessPolicyService.load(local_settings.policies_path)
    recorder = InMemoryMemoryGovernanceRecorder()
    store = SharedMemoryStore(InMemorySharedMemoryRepository(), policy_service, recorder)
    return store, recorder


@pytest.mark.asyncio
async def test_create_does_not_require_approval(local_settings):
    store, recorder = _store(local_settings)
    record = await store.write(
        agent=_agent(),
        session_id="session-1",
        trace_id="trace-1",
        key="goal-1",
        classification="goal",
        content={"text": "reduce latency"},
    )
    assert record.version == 1
    assert recorder.events[-1].event_type == "memory_write"


@pytest.mark.asyncio
async def test_overwrite_without_approval_is_denied(local_settings):
    store, recorder = _store(local_settings)
    agent = _agent()
    await store.write(
        agent=agent,
        session_id="session-1",
        trace_id="trace-1",
        key="goal-1",
        classification="goal",
        content={"text": "v1"},
    )
    with pytest.raises(MemoryAccessDeniedError):
        await store.write(
            agent=agent,
            session_id="session-1",
            trace_id="trace-2",
            key="goal-1",
            classification="goal",
            content={"text": "v2"},
            approval_status="pending",
        )
    assert recorder.events[-1].event_type == "memory_denied"


@pytest.mark.asyncio
async def test_overwrite_with_approval_increments_version_and_emits_update(local_settings):
    store, recorder = _store(local_settings)
    agent = _agent()
    await store.write(
        agent=agent,
        session_id="session-1",
        trace_id="trace-1",
        key="goal-1",
        classification="goal",
        content={"text": "v1"},
    )
    updated = await store.write(
        agent=agent,
        session_id="session-1",
        trace_id="trace-2",
        key="goal-1",
        classification="goal",
        content={"text": "v2"},
        approval_status="approved",
    )
    assert updated.version == 2
    assert recorder.events[-1].event_type == "memory_update"


@pytest.mark.asyncio
async def test_write_denied_without_shared_access(local_settings):
    store, recorder = _store(local_settings)
    agent = _agent(memory_access=["personal"])
    with pytest.raises(MemoryAccessDeniedError):
        await store.write(
            agent=agent,
            session_id="session-1",
            trace_id="trace-1",
            key="goal-1",
            classification="goal",
            content={},
        )
    assert recorder.events[-1].event_type == "memory_denied"


@pytest.mark.asyncio
async def test_read_denied_without_shared_access(local_settings):
    store, recorder = _store(local_settings)
    with pytest.raises(MemoryAccessDeniedError):
        await store.read(
            requesting_agent=_agent(memory_access=["personal"]),
            session_id="session-1",
            trace_id="trace-1",
        )
    assert recorder.events[-1].event_type == "memory_denied"
