"""Unit tests for FoundryAgentProvider using fake project service / agent factory.

No real Azure AI Foundry or agent-framework connectivity is required:
``FoundryAgentProvider`` depends only on a project-service-like object
exposing ``get_api_client()``/``get_async_project_client()`` and an
injectable ``agent_factory`` callable, so it is fully testable with fakes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.agents.foundry.agent_provider import FoundryAgentProvider
from app.agents.foundry.errors import FoundryUnavailableError
from app.agents.models import AgentDefinition, AgentToolDefinition, AgentToolParameter
from app.agents.tool_execution import AgentToolRegistry, ToolCallContext


class _FakeApiClient:
    def __init__(self, *, latest_version: str = "3") -> None:
        self._latest_version = latest_version
        self.requested_versions_for: list[str] = []

    def get_latest_version(self, agent_id: str) -> str:
        self.requested_versions_for.append(agent_id)
        return self._latest_version

    def agent_exists(self, agent_id: str) -> bool:  # pragma: no cover - unused here
        return True

    def create_agent(self, *, name: str, model: str, instructions: str) -> str:  # pragma: no cover
        return name

    def delete_agent(self, agent_id: str) -> None:  # pragma: no cover - unused here
        return None


@dataclass
class _FakeProjectService:
    api_client: _FakeApiClient
    async_project_client: object = field(default_factory=object)

    def get_api_client(self) -> _FakeApiClient:
        return self.api_client

    def get_async_project_client(self) -> object:
        return self.async_project_client


@dataclass
class _FakeAgentResponse:
    text: str


class _FakeFoundryAgent:
    """Stands in for ``agent_framework.foundry.FoundryAgent``."""

    def __init__(self, *, project_client: Any, agent_name: str, agent_version: str) -> None:
        self.project_client = project_client
        self.agent_name = agent_name
        self.agent_version = agent_version
        self.run_calls: list[dict[str, Any]] = []

    async def run(self, messages: Any, *, tools: Any = None) -> _FakeAgentResponse:
        self.run_calls.append({"messages": messages, "tools": tools})
        return _FakeAgentResponse(text="Here is the answer.")


def _agent_factory_returning(agent: _FakeFoundryAgent):
    def _factory(**kwargs: Any) -> _FakeFoundryAgent:
        agent.project_client = kwargs["project_client"]
        agent.agent_name = kwargs["agent_name"]
        agent.agent_version = kwargs["agent_version"]
        return agent

    return _factory


async def test_run_returns_output_text_and_resolves_latest_version_when_unpinned():
    api_client = _FakeApiClient(latest_version="7")
    project_service = _FakeProjectService(api_client=api_client)
    fake_agent = _FakeFoundryAgent(project_client=None, agent_name="", agent_version="")
    provider = FoundryAgentProvider(
        project_service, agent_factory=_agent_factory_returning(fake_agent)
    )

    result = await provider.run(foundry_agent_id="requirements-analyst", input_text="hello")

    assert result.output_text == "Here is the answer."
    assert result.raw_status == "completed"
    assert api_client.requested_versions_for == ["requirements-analyst"]
    assert fake_agent.agent_version == "7"
    assert fake_agent.agent_name == "requirements-analyst"


async def test_run_uses_pinned_version_from_tool_context_without_resolving_latest():
    api_client = _FakeApiClient(latest_version="7")
    project_service = _FakeProjectService(api_client=api_client)
    fake_agent = _FakeFoundryAgent(project_client=None, agent_name="", agent_version="")
    provider = FoundryAgentProvider(
        project_service, agent_factory=_agent_factory_returning(fake_agent)
    )
    agent_definition = AgentDefinition(
        id="requirements-analyst",
        name="Requirements Analyst",
        role="analyst",
        description="Gathers requirements.",
        foundry_agent_id="requirements-analyst",
        foundry_agent_version="2",
    )
    tool_context = ToolCallContext(agent=agent_definition, session_id="s1", trace_id="t1")

    result = await provider.run(
        foundry_agent_id="requirements-analyst", input_text="hello", tool_context=tool_context
    )

    assert result.output_text == "Here is the answer."
    assert api_client.requested_versions_for == []
    assert fake_agent.agent_version == "2"


async def test_run_builds_function_tools_from_agent_tool_definitions():
    api_client = _FakeApiClient(latest_version="1")
    project_service = _FakeProjectService(api_client=api_client)
    fake_agent = _FakeFoundryAgent(project_client=None, agent_name="", agent_version="")
    tool_registry = AgentToolRegistry()

    async def _record_requirements(arguments: dict, context: ToolCallContext) -> dict:
        return {"recorded": True, "text": arguments["text"]}

    tool_registry.register(
        agent_id="requirements-analyst", tool_name="record_requirements", fn=_record_requirements
    )
    agent_definition = AgentDefinition(
        id="requirements-analyst",
        name="Requirements Analyst",
        role="analyst",
        description="Gathers requirements.",
        foundry_agent_id="requirements-analyst",
        tool_definitions=[
            AgentToolDefinition(
                name="record_requirements",
                description="Record a requirement.",
                parameters=[
                    AgentToolParameter(name="text", type="string", description="The requirement text.")
                ],
            )
        ],
    )
    tool_context = ToolCallContext(agent=agent_definition, session_id="s1", trace_id="t1")
    provider = FoundryAgentProvider(
        project_service,
        tool_registry=tool_registry,
        agent_factory=_agent_factory_returning(fake_agent),
    )

    await provider.run(
        foundry_agent_id="requirements-analyst", input_text="hello", tool_context=tool_context
    )

    [run_call] = fake_agent.run_calls
    [built_tool] = run_call["tools"]
    assert built_tool.name == "record_requirements"

    invoked = await built_tool.func(text="a requirement")
    assert invoked == {"recorded": True, "text": "a requirement"}


async def test_run_raises_foundry_unavailable_when_no_output_text():
    api_client = _FakeApiClient()
    project_service = _FakeProjectService(api_client=api_client)
    fake_agent = _FakeFoundryAgent(project_client=None, agent_name="", agent_version="")

    async def _empty_run(messages: Any, *, tools: Any = None) -> _FakeAgentResponse:
        return _FakeAgentResponse(text="")

    fake_agent.run = _empty_run  # type: ignore[method-assign]
    provider = FoundryAgentProvider(
        project_service, agent_factory=_agent_factory_returning(fake_agent)
    )

    with pytest.raises(FoundryUnavailableError, match="no output text"):
        await provider.run(foundry_agent_id="agent-123", input_text="hello")


async def test_run_wraps_unexpected_exceptions_as_foundry_unavailable():
    class _BoomProjectService:
        def get_api_client(self):
            raise RuntimeError("credential expired")

        def get_async_project_client(self):  # pragma: no cover - not reached
            raise RuntimeError("credential expired")

    provider = FoundryAgentProvider(_BoomProjectService())

    with pytest.raises(FoundryUnavailableError, match="credential expired"):
        await provider.run(foundry_agent_id="agent-123", input_text="hello")

