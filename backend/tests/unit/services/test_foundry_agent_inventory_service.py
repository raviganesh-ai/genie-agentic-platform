"""Unit tests for FoundryAgentInventoryService."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.agents.models import AgentDefinition
from app.models.synchronization_result import SynchronizationResult
from app.services.foundry_agent_inventory_service import (
    FoundryAgentInventoryService,
    UnknownInventoryAgentError,
)


def _agent(**overrides: object) -> AgentDefinition:
    defaults: dict[str, object] = {
        "id": "agent-a",
        "name": "Agent A",
        "role": "test_role",
        "description": "A test agent.",
        "foundry_agent_id": "agent-a-foundry",
        "owner": "test-team",
        "governance_policy_id": "standard-governance-policy-v1",
        "prompt_template_ref": "test-prompt",
        "memory_access": ["shared"],
    }
    defaults.update(overrides)
    return AgentDefinition.model_validate(defaults)


async def test_seed_from_agent_creates_a_baseline_record():
    service = FoundryAgentInventoryService()

    record = await service.seed_from_agent(_agent())

    assert record.agent_id == "agent-a"
    assert record.owner == "test-team"
    assert record.lifecycle_state == "draft"
    assert record.synchronization_status == "provisioning_required"
    assert record.validation_status == "not_validated"


async def test_seed_from_agent_is_idempotent():
    service = FoundryAgentInventoryService()
    first = await service.seed_from_agent(_agent())
    await service.record_lifecycle_state("agent-a", "registered")

    second = await service.seed_from_agent(_agent())

    assert second.lifecycle_state == "registered"
    assert first.agent_id == second.agent_id


async def test_get_raises_for_unseeded_agent():
    service = FoundryAgentInventoryService()

    with pytest.raises(UnknownInventoryAgentError):
        await service.get("unknown-agent")


async def test_record_synchronization_result_updates_status_and_timestamp():
    service = FoundryAgentInventoryService()
    await service.seed_from_agent(_agent())
    result = SynchronizationResult(
        agent_id="agent-a", status="synchronized", synchronized_at=datetime.now(UTC)
    )

    await service.record_synchronization_result(result)
    record = await service.get("agent-a")

    assert record.synchronization_status == "synchronized"
    assert record.last_synchronization_time == result.synchronized_at


async def test_record_validation_status_updates_status_and_timestamp():
    service = FoundryAgentInventoryService()
    await service.seed_from_agent(_agent())

    await service.record_validation_status("agent-a", "passed")
    record = await service.get("agent-a")

    assert record.validation_status == "passed"
    assert record.last_validation_time is not None


async def test_record_lifecycle_state_updates_state():
    service = FoundryAgentInventoryService()
    await service.seed_from_agent(_agent())

    await service.record_lifecycle_state("agent-a", "provisioned")
    record = await service.get("agent-a")

    assert record.lifecycle_state == "provisioned"


async def test_list_returns_every_seeded_record():
    service = FoundryAgentInventoryService()
    await service.seed_from_agent(_agent(id="agent-a"))
    await service.seed_from_agent(_agent(id="agent-b"))

    records = await service.list()

    assert sorted(record.agent_id for record in records) == ["agent-a", "agent-b"]
