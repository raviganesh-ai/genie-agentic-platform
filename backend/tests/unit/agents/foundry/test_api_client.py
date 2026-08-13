"""Unit tests for AzureAIProjectsApiClient.

No real Azure AI Foundry connectivity is required here: fakes stand in for
``sdk_client.agents`` (the versioned ``AgentsOperations`` surface -
``get``/``create_version``/``delete``), since ``AzureAIProjectsApiClient``
only calls duck-typed methods on whatever object it is given.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.agents.foundry.api_client import AzureAIProjectsApiClient


@dataclass
class _FakeVersionDetails:
    name: str
    version: str = ""


@dataclass
class _FakeAgentVersions:
    latest: _FakeVersionDetails


@dataclass
class _FakeAgentDetails:
    versions: _FakeAgentVersions


class _FakeAgentsOperations:
    def __init__(self, *, known_agents: dict[str, str]) -> None:
        # known_agents maps agent_name -> latest version string.
        self._known_agents = dict(known_agents)
        self.requested_names: list[str] = []
        self.created: list[dict[str, object]] = []
        self.deleted_names: list[str] = []

    def get(self, agent_name: str) -> _FakeAgentDetails:
        self.requested_names.append(agent_name)
        if agent_name not in self._known_agents:
            raise RuntimeError(f"agent '{agent_name}' not found")
        return _FakeAgentDetails(
            versions=_FakeAgentVersions(
                latest=_FakeVersionDetails(name=agent_name, version=self._known_agents[agent_name])
            )
        )

    def create_version(
        self, agent_name: str, *, definition: object, description: str | None = None
    ) -> _FakeVersionDetails:
        self.created.append(
            {"agent_name": agent_name, "definition": definition, "description": description}
        )
        return _FakeVersionDetails(name=agent_name)

    def delete(self, agent_name: str) -> None:
        self.deleted_names.append(agent_name)


class _FakeSdkClient:
    def __init__(self, *, known_agents: dict[str, str]) -> None:
        self.agents = _FakeAgentsOperations(known_agents=known_agents)


def test_agent_exists_returns_true_for_a_known_agent():
    sdk_client = _FakeSdkClient(known_agents={"requirements-analyst": "3"})
    client = AzureAIProjectsApiClient(sdk_client)

    assert client.agent_exists("requirements-analyst") is True
    assert sdk_client.agents.requested_names == ["requirements-analyst"]


def test_agent_exists_returns_false_for_an_unknown_agent():
    sdk_client = _FakeSdkClient(known_agents={"requirements-analyst": "3"})
    client = AzureAIProjectsApiClient(sdk_client)

    assert client.agent_exists("nonexistent-agent") is False


def test_agent_exists_returns_false_on_any_lookup_failure():
    class _AlwaysFailsAgentsOperations:
        def get(self, agent_name: str) -> object:
            raise ConnectionError("network unreachable")

    class _AlwaysFailsSdkClient:
        def __init__(self) -> None:
            self.agents = _AlwaysFailsAgentsOperations()

    client = AzureAIProjectsApiClient(_AlwaysFailsSdkClient())

    assert client.agent_exists("any-agent") is False


def test_get_latest_version_returns_the_resolved_version_string():
    sdk_client = _FakeSdkClient(known_agents={"requirements-analyst": "7"})
    client = AzureAIProjectsApiClient(sdk_client)

    assert client.get_latest_version("requirements-analyst") == "7"


def test_create_agent_creates_a_version_and_returns_its_name():
    sdk_client = _FakeSdkClient(known_agents={})
    client = AzureAIProjectsApiClient(sdk_client)

    result = client.create_agent(
        name="requirements-analyst-cx-abc123", model="gpt-4o", instructions="Be helpful."
    )

    assert result == "requirements-analyst-cx-abc123"
    assert sdk_client.agents.created == [
        {
            "agent_name": "requirements-analyst-cx-abc123",
            "definition": sdk_client.agents.created[0]["definition"],
            "description": None,
        }
    ]


def test_create_agent_forwards_description_as_the_human_readable_agent_name():
    sdk_client = _FakeSdkClient(known_agents={})
    client = AzureAIProjectsApiClient(sdk_client)

    client.create_agent(
        name="acme-mission-a1b2c3d4-requirements-specialist",
        model="gpt-4o",
        instructions="Be helpful.",
        description="Requirements Specialist",
    )

    assert sdk_client.agents.created[0]["description"] == "Requirements Specialist"


def test_delete_agent_deletes_by_name():
    sdk_client = _FakeSdkClient(known_agents={"requirements-analyst-cx-abc123": "1"})
    client = AzureAIProjectsApiClient(sdk_client)

    client.delete_agent("requirements-analyst-cx-abc123")

    assert sdk_client.agents.deleted_names == ["requirements-analyst-cx-abc123"]

