"""Unit tests for FoundryAgentSynchronizationService.

Uses fake project-service/api-client objects (same pattern as
``test_agent_provider.py``) so no real Azure AI Foundry connectivity is
required.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.agents.foundry.agent_synchronization_service import FoundryAgentSynchronizationService
from app.agents.foundry.errors import FoundryAgentSynchronizationError, FoundryUnavailableError
from app.agents.models import AgentDefinition
from app.agents.registry import AgentRegistry


def _agent(
    *,
    agent_id: str,
    foundry_agent_id: str | None,
    enabled: bool = True,
) -> AgentDefinition:
    return AgentDefinition(
        id=agent_id,
        name=agent_id,
        role="role",
        description="description",
        foundry_agent_id=foundry_agent_id,
        enabled=enabled,
    )


def _registry(*agents: AgentDefinition) -> AgentRegistry:
    return AgentRegistry({agent.id: agent for agent in agents})


@dataclass
class _FakeAgentApiClient:
    known_agent_ids: set[str]
    unreachable_agent_ids: set[str] = field(default_factory=set)
    requested_ids: list[str] = field(default_factory=list)

    def agent_exists(self, agent_id: str) -> bool:
        self.requested_ids.append(agent_id)
        if agent_id in self.unreachable_agent_ids:
            raise FoundryUnavailableError(f"transient failure looking up '{agent_id}'")
        return agent_id in self.known_agent_ids


@dataclass
class _FakeProjectService:
    api_client: _FakeAgentApiClient | None = None
    unavailable: bool = False

    def get_api_client(self) -> _FakeAgentApiClient:
        if self.unavailable or self.api_client is None:
            raise FoundryUnavailableError("Azure AI Foundry is not reachable.")
        return self.api_client


def test_synchronize_succeeds_when_every_referenced_agent_exists():
    api_client = _FakeAgentApiClient(known_agent_ids={"agent-a-foundry", "agent-b-foundry"})
    registry = _registry(
        _agent(agent_id="agent-a", foundry_agent_id="agent-a-foundry"),
        _agent(agent_id="agent-b", foundry_agent_id="agent-b-foundry"),
    )
    service = FoundryAgentSynchronizationService(_FakeProjectService(api_client=api_client))

    service.synchronize(registry)

    assert sorted(api_client.requested_ids) == ["agent-a-foundry", "agent-b-foundry"]


def test_synchronize_skips_agents_without_a_foundry_agent_id():
    api_client = _FakeAgentApiClient(known_agent_ids=set())
    registry = _registry(_agent(agent_id="local-only-agent", foundry_agent_id=None))
    service = FoundryAgentSynchronizationService(_FakeProjectService(api_client=api_client))

    service.synchronize(registry)

    assert api_client.requested_ids == []


def test_synchronize_skips_disabled_agents():
    api_client = _FakeAgentApiClient(known_agent_ids=set())
    registry = _registry(
        _agent(agent_id="disabled-agent", foundry_agent_id="disabled-agent-foundry", enabled=False)
    )
    service = FoundryAgentSynchronizationService(_FakeProjectService(api_client=api_client))

    service.synchronize(registry)

    assert api_client.requested_ids == []


def test_synchronize_raises_when_a_referenced_agent_does_not_exist():
    api_client = _FakeAgentApiClient(known_agent_ids=set())
    registry = _registry(_agent(agent_id="agent-a", foundry_agent_id="missing-in-foundry"))
    service = FoundryAgentSynchronizationService(_FakeProjectService(api_client=api_client))

    with pytest.raises(FoundryAgentSynchronizationError, match="missing-in-foundry"):
        service.synchronize(registry)


def test_synchronize_raises_when_the_lookup_itself_is_unreachable():
    api_client = _FakeAgentApiClient(
        known_agent_ids=set(), unreachable_agent_ids={"agent-a-foundry"}
    )
    registry = _registry(_agent(agent_id="agent-a", foundry_agent_id="agent-a-foundry"))
    service = FoundryAgentSynchronizationService(_FakeProjectService(api_client=api_client))

    with pytest.raises(FoundryAgentSynchronizationError, match="agent-a-foundry"):
        service.synchronize(registry)


def test_synchronize_raises_when_foundry_is_completely_unreachable():
    registry = _registry(_agent(agent_id="agent-a", foundry_agent_id="agent-a-foundry"))
    service = FoundryAgentSynchronizationService(_FakeProjectService(unavailable=True))

    with pytest.raises(FoundryAgentSynchronizationError, match="not reachable"):
        service.synchronize(registry)


def test_synchronize_reports_every_failing_agent_in_one_error():
    api_client = _FakeAgentApiClient(known_agent_ids={"agent-b-foundry"})
    registry = _registry(
        _agent(agent_id="agent-a", foundry_agent_id="agent-a-missing"),
        _agent(agent_id="agent-b", foundry_agent_id="agent-b-foundry"),
        _agent(agent_id="agent-c", foundry_agent_id="agent-c-missing"),
    )
    service = FoundryAgentSynchronizationService(_FakeProjectService(api_client=api_client))

    with pytest.raises(FoundryAgentSynchronizationError) as exc_info:
        service.synchronize(registry)

    message = str(exc_info.value)
    assert "agent-a-missing" in message
    assert "agent-c-missing" in message
    assert "agent-b-foundry" not in message
