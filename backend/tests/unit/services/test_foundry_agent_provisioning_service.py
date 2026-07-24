"""Unit tests for FoundryAgentProvisioningService."""
from __future__ import annotations

import pytest

from app.agents.models import AgentDefinition
from app.config.settings import Settings
from app.governance.governance_service import create_governance_service
from app.services.foundry_agent_inventory_service import FoundryAgentInventoryService
from app.services.foundry_agent_lifecycle_service import FoundryAgentLifecycleService
from app.services.foundry_agent_provisioning_service import (
    AgentProvisioningError,
    FoundryAgentProvisioningService,
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
    }
    defaults.update(overrides)
    return AgentDefinition.model_validate(defaults)


def _service(local_settings: Settings) -> FoundryAgentProvisioningService:
    inventory_service = FoundryAgentInventoryService()
    governance_service = create_governance_service(settings=local_settings)
    lifecycle_service = FoundryAgentLifecycleService(
        governance_service=governance_service, inventory_service=inventory_service
    )
    return FoundryAgentProvisioningService(
        inventory_service=inventory_service,
        lifecycle_service=lifecycle_service,
        governance_service=governance_service,
    )


async def test_register_raises_without_foundry_agent_id(local_settings: Settings):
    service = _service(local_settings)

    with pytest.raises(AgentProvisioningError):
        await service.register(
            _agent(foundry_agent_id=None), session_id="session-1", trace_id="trace-1"
        )


async def test_register_transitions_draft_to_registered_and_builds_manifest(
    local_settings: Settings,
):
    service = _service(local_settings)

    manifest = await service.register(_agent(), session_id="session-1", trace_id="trace-1")

    assert manifest.agent_id == "agent-a"
    assert manifest.lifecycle_state == "registered"
    assert manifest.owner == "test-team"
    assert manifest.foundry_agent_reference == "agent-a-foundry"
    assert manifest.governance_policy_id == "standard-governance-policy-v1"


async def test_mark_provisioned_transitions_registered_to_provisioned(local_settings: Settings):
    service = _service(local_settings)
    agent = _agent()
    await service.register(agent, session_id="session-1", trace_id="trace-1")

    manifest = await service.mark_provisioned(agent, session_id="session-1", trace_id="trace-1")

    assert manifest.lifecycle_state == "provisioned"
