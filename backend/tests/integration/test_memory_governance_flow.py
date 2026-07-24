"""End-to-end governance flow across all three memory tiers."""
from __future__ import annotations

import pytest

from app.agents.models import AgentDefinition
from app.memory.memory_events import InMemoryMemoryGovernanceRecorder
from app.memory.memory_service import create_memory_service


def _agent(agent_id: str = "agent-a") -> AgentDefinition:
    return AgentDefinition(
        id=agent_id,
        name=agent_id,
        role="test_role",
        description="A test agent.",
        memory_access=["personal", "shared", "enterprise"],
        version="1.4.0",
    )


@pytest.mark.asyncio
async def test_full_flow_emits_governance_events_across_all_tiers(local_settings):
    recorder = InMemoryMemoryGovernanceRecorder()
    service = create_memory_service(settings=local_settings, governance_recorder=recorder)
    agent = _agent()

    await service.personal.write(
        agent=agent,
        session_id="session-1",
        trace_id="trace-1",
        classification="observation",
        content={"note": "initial observation"},
    )
    await service.personal.read(
        requesting_agent=agent,
        owning_agent_id=agent.id,
        session_id="session-1",
        trace_id="trace-2",
    )

    await service.shared.write(
        agent=agent,
        session_id="session-1",
        trace_id="trace-3",
        key="requirement-1",
        classification="requirement",
        content={"text": "must support 10k concurrent sessions"},
    )
    await service.shared.read(requesting_agent=agent, session_id="session-1", trace_id="trace-4")

    await service.enterprise.promote(
        agent=agent,
        record_id="pattern-1",
        session_id="session-1",
        trace_id="trace-5",
        classification="reusable_knowledge",
        content={"summary": "session-scoped shared memory pattern"},
        approval_status="approved",
    )
    await service.enterprise.search(
        agent=agent, query="pattern", session_id="session-1", trace_id="trace-6"
    )

    event_types = [event.event_type for event in recorder.events]
    assert event_types.count("memory_write") == 3
    assert event_types.count("memory_read") == 3

    tiers = {event.tier for event in recorder.events}
    assert tiers == {"personal", "shared", "enterprise"}

    trace_ids = {event.trace_id for event in recorder.events}
    assert trace_ids == {f"trace-{i}" for i in range(1, 7)}
