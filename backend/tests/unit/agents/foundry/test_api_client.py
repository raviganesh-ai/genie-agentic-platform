"""Unit tests for AzureAIProjectsApiClient.agent_exists.

No real Azure AI Foundry connectivity or azure-ai-projects import is
required here: ``AzureAIProjectsApiClient`` only calls
``sdk_client.agents.get_agent(agent_id)`` on whatever object it is given, so
a plain fake stands in for the real SDK client.
"""
from __future__ import annotations

from app.agents.foundry.api_client import AzureAIProjectsApiClient


class _FakeAgentsOperations:
    def __init__(self, *, known_agent_ids: set[str]) -> None:
        self._known_agent_ids = known_agent_ids
        self.requested_ids: list[str] = []

    def get_agent(self, agent_id: str) -> object:
        self.requested_ids.append(agent_id)
        if agent_id not in self._known_agent_ids:
            raise RuntimeError(f"agent '{agent_id}' not found")
        return object()


class _FakeSdkClient:
    def __init__(self, *, known_agent_ids: set[str]) -> None:
        self.agents = _FakeAgentsOperations(known_agent_ids=known_agent_ids)


def test_agent_exists_returns_true_for_a_known_agent():
    sdk_client = _FakeSdkClient(known_agent_ids={"requirements-analyst-agent"})
    client = AzureAIProjectsApiClient(sdk_client)

    assert client.agent_exists("requirements-analyst-agent") is True
    assert sdk_client.agents.requested_ids == ["requirements-analyst-agent"]


def test_agent_exists_returns_false_for_an_unknown_agent():
    sdk_client = _FakeSdkClient(known_agent_ids={"requirements-analyst-agent"})
    client = AzureAIProjectsApiClient(sdk_client)

    assert client.agent_exists("nonexistent-agent") is False


def test_agent_exists_returns_false_on_any_lookup_failure():
    class _AlwaysFailsAgentsOperations:
        def get_agent(self, agent_id: str) -> object:
            raise ConnectionError("network unreachable")

    class _AlwaysFailsSdkClient:
        def __init__(self) -> None:
            self.agents = _AlwaysFailsAgentsOperations()

    client = AzureAIProjectsApiClient(_AlwaysFailsSdkClient())

    assert client.agent_exists("any-agent") is False
