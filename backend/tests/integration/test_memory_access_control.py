"""Cross-agent access control scenarios spanning all three memory tiers."""
from __future__ import annotations

import pytest

from app.agents.models import AgentDefinition
from app.memory.memory_events import InMemoryMemoryGovernanceRecorder
from app.memory.memory_models import MemoryAccessDeniedError
from app.memory.memory_service import create_memory_service


def _agent(agent_id: str, memory_access: list[str]) -> AgentDefinition:
    return AgentDefinition(
        id=agent_id,
        name=agent_id,
        role="test_role",
        description="A test agent.",
        memory_access=memory_access,
    )


@pytest.mark.asyncio
async def test_agent_cannot_read_another_agents_personal_memory(local_settings):
    recorder = InMemoryMemoryGovernanceRecorder()
    service = create_memory_service(settings=local_settings, governance_recorder=recorder)

    owner = _agent("agent-a", ["personal"])
    intruder = _agent("agent-b", ["personal"])

    await service.personal.write(
        agent=owner,
        session_id="session-1",
        trace_id="trace-1",
        classification="finding",
        content={"secret": "confidential customer detail"},
    )

    with pytest.raises(MemoryAccessDeniedError):
        await service.personal.read(
            requesting_agent=intruder,
            owning_agent_id=owner.id,
            session_id="session-1",
            trace_id="trace-2",
        )
    assert recorder.events[-1].event_type == "memory_denied"


@pytest.mark.asyncio
async def test_agent_without_shared_access_is_denied(local_settings):
    recorder = InMemoryMemoryGovernanceRecorder()
    service = create_memory_service(settings=local_settings, governance_recorder=recorder)
    agent = _agent("agent-a", ["personal"])

    with pytest.raises(MemoryAccessDeniedError):
        await service.shared.write(
            agent=agent,
            session_id="session-1",
            trace_id="trace-1",
            key="risk-1",
            classification="risk",
            content={},
        )
    assert recorder.events[-1].event_type == "memory_denied"


@pytest.mark.asyncio
async def test_agent_without_enterprise_access_cannot_promote_or_search(local_settings):
    recorder = InMemoryMemoryGovernanceRecorder()
    service = create_memory_service(settings=local_settings, governance_recorder=recorder)
    agent = _agent("agent-a", ["shared"])

    with pytest.raises(MemoryAccessDeniedError):
        await service.enterprise.promote(
            agent=agent,
            record_id="pattern-1",
            session_id="session-1",
            trace_id="trace-1",
            classification="industry_pattern",
            content={},
            approval_status="approved",
        )

    with pytest.raises(MemoryAccessDeniedError):
        await service.enterprise.search(
            agent=agent, query="pattern", session_id="session-1", trace_id="trace-2"
        )


@pytest.mark.asyncio
async def test_shared_overwrite_denied_without_approval_across_agents(local_settings):
    recorder = InMemoryMemoryGovernanceRecorder()
    service = create_memory_service(settings=local_settings, governance_recorder=recorder)
    first_author = _agent("agent-a", ["shared"])
    second_author = _agent("agent-b", ["shared"])

    await service.shared.write(
        agent=first_author,
        session_id="session-1",
        trace_id="trace-1",
        key="constraint-1",
        classification="constraint",
        content={"text": "must run on Azure"},
    )

    with pytest.raises(MemoryAccessDeniedError):
        await service.shared.write(
            agent=second_author,
            session_id="session-1",
            trace_id="trace-2",
            key="constraint-1",
            classification="constraint",
            content={"text": "must run on Azure and AWS"},
            approval_status="pending",
        )
