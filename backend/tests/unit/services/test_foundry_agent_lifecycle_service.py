"""Unit tests for FoundryAgentLifecycleService."""
from __future__ import annotations

import pytest

from app.agents.models import AgentDefinition
from app.config.settings import Settings
from app.governance.governance_service import create_governance_service
from app.services.foundry_agent_inventory_service import FoundryAgentInventoryService
from app.services.foundry_agent_lifecycle_service import (
    FoundryAgentLifecycleService,
    InvalidLifecycleTransitionError,
)


def _service(
    local_settings: Settings,
) -> tuple[FoundryAgentLifecycleService, FoundryAgentInventoryService]:
    inventory_service = FoundryAgentInventoryService()
    governance_service = create_governance_service(settings=local_settings)
    lifecycle_service = FoundryAgentLifecycleService(
        governance_service=governance_service, inventory_service=inventory_service
    )
    return lifecycle_service, inventory_service


async def test_current_state_defaults_to_draft(local_settings: Settings):
    lifecycle_service, _ = _service(local_settings)

    assert await lifecycle_service.current_state("agent-a") == "draft"


async def test_legal_transition_updates_state_and_history(local_settings: Settings):
    lifecycle_service, inventory_service = _service(local_settings)
    await inventory_service.seed_from_agent(
        AgentDefinition(id="agent-a", name="Agent A", role="test_role", description="Test agent.")
    )

    transition = await lifecycle_service.transition(
        agent_id="agent-a",
        to_state="registered",
        reason="Initial registration.",
        session_id="session-1",
        trace_id="trace-1",
    )

    assert transition.from_state == "draft"
    assert transition.to_state == "registered"
    assert await lifecycle_service.current_state("agent-a") == "registered"
    history = await lifecycle_service.history("agent-a")
    assert history == [transition]


async def test_transition_updates_inventory_lifecycle_state(local_settings: Settings):
    lifecycle_service, inventory_service = _service(local_settings)
    await inventory_service.seed_from_agent(
        AgentDefinition(id="agent-a", name="Agent A", role="test_role", description="Test agent.")
    )

    await lifecycle_service.transition(
        agent_id="agent-a",
        to_state="registered",
        reason="Initial registration.",
        session_id="session-1",
        trace_id="trace-1",
    )

    record = await inventory_service.get("agent-a")
    assert record.lifecycle_state == "registered"


async def test_illegal_transition_raises(local_settings: Settings):
    lifecycle_service, _ = _service(local_settings)

    with pytest.raises(InvalidLifecycleTransitionError):
        await lifecycle_service.transition(
            agent_id="agent-a",
            to_state="validated",
            reason="Skip ahead illegally.",
            session_id="session-1",
            trace_id="trace-1",
        )


async def test_retired_is_a_terminal_state(local_settings: Settings):
    lifecycle_service, inventory_service = _service(local_settings)
    await inventory_service.seed_from_agent(
        AgentDefinition(id="agent-a", name="Agent A", role="test_role", description="Test agent.")
    )

    for to_state in ("registered", "provisioned", "deprecated", "retired"):
        await lifecycle_service.transition(
            agent_id="agent-a",
            to_state=to_state,
            reason="Progressing lifecycle.",
            session_id="session-1",
            trace_id="trace-1",
        )

    with pytest.raises(InvalidLifecycleTransitionError):
        await lifecycle_service.transition(
            agent_id="agent-a",
            to_state="provisioned",
            reason="Cannot leave retired.",
            session_id="session-1",
            trace_id="trace-1",
        )
