"""Unit tests for CustomerAgentProvisioningService.

Uses a fake project-service/api-client pair (same pattern as
``test_foundry_agent_synchronization_service.py``) so no real Azure AI
Foundry connectivity is required. Governance events are verified against a
real ``GovernanceService`` (local provider) built via
``create_governance_service``, the same pattern used in
``test_foundry_agent_provisioning_service.py``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.agents.foundry.errors import FoundryUnavailableError
from app.agents.models import AgentDefinition
from app.agents.registry import AgentRegistry
from app.config.settings import Settings
from app.governance.governance_service import create_governance_service
from app.services.customer_agent_provisioning_service import (
    CustomerAgentProvisioningError,
    CustomerAgentProvisioningService,
    NullCustomerAgentProvisioningService,
    create_customer_agent_provisioning_service,
)


def _agent(
    *,
    agent_id: str,
    foundry_agent_id: str | None = "shared-foundry-id",
    enabled: bool = True,
) -> AgentDefinition:
    return AgentDefinition(
        id=agent_id,
        name=agent_id,
        role="test_role",
        description=f"Description for {agent_id}.",
        foundry_agent_id=foundry_agent_id,
        model_deployment_ref="test-model",
        enabled=enabled,
    )


def _registry(*agents: AgentDefinition) -> AgentRegistry:
    return AgentRegistry({agent.id: agent for agent in agents})


@dataclass
class _FakeAgentApiClient:
    fail_on_create_name: str | None = None
    created: list[dict[str, str]] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    _next_id: int = 0

    def create_agent(
        self, *, name: str, model: str, instructions: str, description: str | None = None
    ) -> str:
        if self.fail_on_create_name is not None and name == self.fail_on_create_name:
            raise RuntimeError(f"simulated failure creating '{name}'")
        self._next_id += 1
        dedicated_id = f"dedicated-{self._next_id}"
        self.created.append(
            {
                "name": name,
                "model": model,
                "instructions": instructions,
                "id": dedicated_id,
                "description": description or "",
            }
        )
        return dedicated_id

    def delete_agent(self, agent_id: str) -> None:
        self.deleted.append(agent_id)


@dataclass
class _FakeProjectService:
    api_client: _FakeAgentApiClient
    unavailable: bool = False

    def get_api_client(self) -> _FakeAgentApiClient:
        if self.unavailable:
            raise FoundryUnavailableError("Azure AI Foundry is not reachable.")
        return self.api_client


def _service(
    *, api_client: _FakeAgentApiClient, registry: AgentRegistry, local_settings: Settings
) -> tuple[CustomerAgentProvisioningService, _FakeAgentApiClient]:
    governance_service = create_governance_service(settings=local_settings)
    service = CustomerAgentProvisioningService(
        agent_registry=registry,
        project_service=_FakeProjectService(api_client=api_client),
        governance_service=governance_service,
    )
    return service, api_client


async def test_provision_creates_one_dedicated_agent_per_enabled_catalog_agent(
    local_settings: Settings,
):
    registry = _registry(
        _agent(agent_id="agent-a", foundry_agent_id="agent-a-foundry"),
        _agent(agent_id="agent-b", foundry_agent_id="agent-b-foundry"),
    )
    service, api_client = _service(
        api_client=_FakeAgentApiClient(), registry=registry, local_settings=local_settings
    )

    records = await service.provision_for_session(session_id="session-1", trace_id="trace-1")

    assert {record.agent_id for record in records} == {"agent-a", "agent-b"}
    assert len(api_client.created) == 2
    assert service.is_provisioned("session-1")
    assert service.resolve(session_id="session-1", agent_id="agent-a") is not None
    # The exact human-readable agent name (AgentDefinition.name) must be
    # forwarded as `description` so it stays visible in the Foundry portal
    # even though the resource `name` itself is slugified with a per-session suffix.
    assert {created["description"] for created in api_client.created} == {"agent-a", "agent-b"}



async def test_provision_skips_disabled_and_local_only_agents(local_settings: Settings):
    registry = _registry(
        _agent(agent_id="agent-a", foundry_agent_id="agent-a-foundry"),
        _agent(agent_id="disabled-agent", foundry_agent_id="disabled-foundry", enabled=False),
        _agent(agent_id="local-only-agent", foundry_agent_id=None),
    )
    service, api_client = _service(
        api_client=_FakeAgentApiClient(), registry=registry, local_settings=local_settings
    )

    records = await service.provision_for_session(session_id="session-1", trace_id="trace-1")

    assert {record.agent_id for record in records} == {"agent-a"}
    assert len(api_client.created) == 1


async def test_provision_is_idempotent_for_an_already_provisioned_session(
    local_settings: Settings,
):
    registry = _registry(_agent(agent_id="agent-a", foundry_agent_id="agent-a-foundry"))
    service, api_client = _service(
        api_client=_FakeAgentApiClient(), registry=registry, local_settings=local_settings
    )

    first = await service.provision_for_session(session_id="session-1", trace_id="trace-1")
    second = await service.provision_for_session(session_id="session-1", trace_id="trace-2")

    assert first == second
    assert len(api_client.created) == 1


async def test_provision_records_a_provisioned_lifecycle_event_per_agent(
    local_settings: Settings,
):
    registry = _registry(_agent(agent_id="agent-a", foundry_agent_id="agent-a-foundry"))
    service, _ = _service(
        api_client=_FakeAgentApiClient(), registry=registry, local_settings=local_settings
    )
    governance_service = service._governance_service

    await service.provision_for_session(session_id="session-1", trace_id="trace-1")

    events = await governance_service.events_for_session("session-1")
    lifecycle_events = [e for e in events if e.category == "agent_lifecycle"]
    assert len(lifecycle_events) == 1
    assert lifecycle_events[0].detail["state"] == "provisioned"


async def test_provision_rolls_back_already_created_agents_on_failure(local_settings: Settings):
    registry = _registry(
        _agent(agent_id="agent-a", foundry_agent_id="agent-a-foundry"),
        _agent(agent_id="agent-b", foundry_agent_id="agent-b-foundry"),
    )
    api_client = _FakeAgentApiClient()
    # Force the second agent's create_agent call to fail regardless of the
    # exact generated name by monkeypatching after construction.
    original_create = api_client.create_agent
    call_count = {"n": 0}

    def _flaky_create(
        *, name: str, model: str, instructions: str, description: str | None = None
    ) -> str:
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("simulated failure on second agent")
        return original_create(name=name, model=model, instructions=instructions, description=description)

    api_client.create_agent = _flaky_create  # type: ignore[method-assign]

    service, _ = _service(api_client=api_client, registry=registry, local_settings=local_settings)

    with pytest.raises(CustomerAgentProvisioningError):
        await service.provision_for_session(session_id="session-1", trace_id="trace-1")

    assert not service.is_provisioned("session-1")
    assert len(api_client.created) == 1
    assert api_client.deleted == [api_client.created[0]["id"]]


async def test_provision_raises_when_foundry_is_unreachable(local_settings: Settings):
    registry = _registry(_agent(agent_id="agent-a", foundry_agent_id="agent-a-foundry"))
    governance_service = create_governance_service(settings=local_settings)
    service = CustomerAgentProvisioningService(
        agent_registry=registry,
        project_service=_FakeProjectService(api_client=_FakeAgentApiClient(), unavailable=True),
        governance_service=governance_service,
    )

    with pytest.raises(CustomerAgentProvisioningError):
        await service.provision_for_session(session_id="session-1", trace_id="trace-1")


async def test_deprovision_deletes_every_dedicated_agent_and_clears_state(
    local_settings: Settings,
):
    registry = _registry(
        _agent(agent_id="agent-a", foundry_agent_id="agent-a-foundry"),
        _agent(agent_id="agent-b", foundry_agent_id="agent-b-foundry"),
    )
    service, api_client = _service(
        api_client=_FakeAgentApiClient(), registry=registry, local_settings=local_settings
    )
    records = await service.provision_for_session(session_id="session-1", trace_id="trace-1")

    await service.deprovision_for_session(session_id="session-1", trace_id="trace-2")

    assert sorted(api_client.deleted) == sorted(record.foundry_agent_id for record in records)
    assert not service.is_provisioned("session-1")
    assert service.resolve(session_id="session-1", agent_id="agent-a") is None


async def test_deprovision_is_a_no_op_when_nothing_was_provisioned(local_settings: Settings):
    registry = _registry(_agent(agent_id="agent-a", foundry_agent_id="agent-a-foundry"))
    service, api_client = _service(
        api_client=_FakeAgentApiClient(), registry=registry, local_settings=local_settings
    )

    await service.deprovision_for_session(session_id="never-provisioned", trace_id="trace-1")

    assert api_client.deleted == []


async def test_scope_id_provisions_an_isolated_fleet_distinct_from_the_session_fleet(
    local_settings: Settings,
):
    """Two requirement groups in the same session get fully separate fleets."""

    registry = _registry(_agent(agent_id="agent-a", foundry_agent_id="agent-a-foundry"))
    service, api_client = _service(
        api_client=_FakeAgentApiClient(), registry=registry, local_settings=local_settings
    )

    group_a_records = await service.provision_for_session(
        session_id="session-1", scope_id="group-a", trace_id="trace-1"
    )
    group_b_records = await service.provision_for_session(
        session_id="session-1", scope_id="group-b", trace_id="trace-2"
    )

    assert len(api_client.created) == 2
    assert group_a_records[0].foundry_agent_id != group_b_records[0].foundry_agent_id
    assert service.is_provisioned("session-1", scope_id="group-a")
    assert service.is_provisioned("session-1", scope_id="group-b")
    assert not service.is_provisioned("session-1")
    assert (
        service.resolve(session_id="session-1", agent_id="agent-a", scope_id="group-a")
        == group_a_records[0].foundry_agent_id
    )
    assert (
        service.resolve(session_id="session-1", agent_id="agent-a", scope_id="group-b")
        == group_b_records[0].foundry_agent_id
    )
    # Looking up without a scope_id (session-level) finds neither group's fleet.
    assert service.resolve(session_id="session-1", agent_id="agent-a") is None


async def test_model_scope_id_uses_a_foundry_safe_agent_name(local_settings: Settings):
    registry = _registry(_agent(agent_id="agent-a", foundry_agent_id="agent-a-foundry"))
    service, api_client = _service(
        api_client=_FakeAgentApiClient(), registry=registry, local_settings=local_settings
    )

    await service.provision_for_session(
        session_id="session-1",
        scope_id="model:gpt-5-mini",
        trace_id="trace-1",
        model_deployment_ref="gpt-5-mini",
    )

    assert api_client.created[0]["name"] == "agent-a-cx-model-gpt-5-mini"
    assert api_client.created[0]["model"] == "gpt-5-mini"


async def test_model_override_does_not_clone_the_shared_orchestrator(local_settings: Settings):
    registry = _registry(
        _agent(agent_id="genie-orchestrator", foundry_agent_id="orchestrator-foundry"),
        _agent(agent_id="requirements-analyst", foundry_agent_id="requirements-foundry"),
    )
    service, api_client = _service(
        api_client=_FakeAgentApiClient(), registry=registry, local_settings=local_settings
    )

    await service.provision_for_session(
        session_id="session-1",
        scope_id="model:gpt-5-mini",
        trace_id="trace-1",
        model_deployment_ref="gpt-5-mini",
    )

    created_by_description = {created["description"]: created for created in api_client.created}
    assert "genie-orchestrator" not in created_by_description
    assert created_by_description["requirements-analyst"]["model"] == "gpt-5-mini"


async def test_deprovision_by_scope_id_leaves_other_scopes_untouched(local_settings: Settings):
    registry = _registry(_agent(agent_id="agent-a", foundry_agent_id="agent-a-foundry"))
    service, api_client = _service(
        api_client=_FakeAgentApiClient(), registry=registry, local_settings=local_settings
    )
    await service.provision_for_session(session_id="session-1", scope_id="group-a", trace_id="t1")
    await service.provision_for_session(session_id="session-1", scope_id="group-b", trace_id="t2")

    await service.deprovision_for_session(
        session_id="session-1", scope_id="group-a", trace_id="t3"
    )

    assert not service.is_provisioned("session-1", scope_id="group-a")
    assert service.is_provisioned("session-1", scope_id="group-b")
    assert len(api_client.deleted) == 1



async def test_deprovision_records_a_retired_lifecycle_event_per_agent(local_settings: Settings):
    registry = _registry(_agent(agent_id="agent-a", foundry_agent_id="agent-a-foundry"))
    service, _ = _service(
        api_client=_FakeAgentApiClient(), registry=registry, local_settings=local_settings
    )
    governance_service = service._governance_service
    await service.provision_for_session(session_id="session-1", trace_id="trace-1")

    await service.deprovision_for_session(session_id="session-1", trace_id="trace-2")

    events = await governance_service.events_for_session("session-1")
    lifecycle_events = [e for e in events if e.category == "agent_lifecycle"]
    states = [e.detail["state"] for e in lifecycle_events]
    assert states == ["provisioned", "retired"]


def test_null_service_never_provisions_and_always_resolves_to_none():
    service = NullCustomerAgentProvisioningService()

    assert service.provisioned_agents("any-session") == []
    assert service.resolve(session_id="any-session", agent_id="agent-a") is None
    assert service.is_provisioned("any-session") is False


async def test_null_service_provision_and_deprovision_are_safe_no_ops():
    service = NullCustomerAgentProvisioningService()

    records = await service.provision_for_session(session_id="session-1", trace_id="trace-1")
    await service.deprovision_for_session(session_id="session-1", trace_id="trace-2")

    assert records == []


def test_factory_returns_null_service_in_local_mode_without_foundry_configured(
    local_settings: Settings,
):
    registry = _registry(_agent(agent_id="agent-a"))
    governance_service = create_governance_service(settings=local_settings)

    service = create_customer_agent_provisioning_service(
        settings=local_settings, agent_registry=registry, governance_service=governance_service
    )

    assert isinstance(service, NullCustomerAgentProvisioningService)
