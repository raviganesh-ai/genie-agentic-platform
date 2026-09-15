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
from app.agents.models import (
    AgentDefinition,
    AgentMcpToolDefinition,
    AgentToolDefinition,
    AgentToolParameter,
)
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

    def create_agent(
        self, *, name: str, model: str, instructions: str, description: str | None = None
    ) -> str:  # pragma: no cover
        return name

    def delete_agent(self, agent_id: str) -> None:  # pragma: no cover - unused here
        return None


@dataclass
class _FakeProjectService:
    api_client: _FakeApiClient
    async_project_client: object = field(default_factory=object)
    mcp_access_token: str | None = "fake-access-token"
    mcp_access_token_error: Exception | None = None

    def get_api_client(self) -> _FakeApiClient:
        return self.api_client

    def get_async_project_client(self) -> object:
        return self.async_project_client

    def get_mcp_access_token(self, client_id: str) -> str:
        self.mcp_access_token_requested_for = client_id
        if self.mcp_access_token_error is not None:
            raise self.mcp_access_token_error
        assert self.mcp_access_token is not None
        return self.mcp_access_token


@dataclass
class _FakeAgentResponse:
    text: str


@dataclass
class _FakeAgentUpdate:
    text: str


class _FakeFoundryAgent:
    """Stands in for ``agent_framework.foundry.FoundryAgent``."""

    def __init__(self, *, project_client: Any, agent_name: str, agent_version: str) -> None:
        self.project_client = project_client
        self.agent_name = agent_name
        self.agent_version = agent_version
        self.run_calls: list[dict[str, Any]] = []
        self.stream_chunks: list[str] = ["Here ", "is ", "the answer."]

    def run(self, messages: Any, *, tools: Any = None, stream: bool = False) -> Any:
        self.run_calls.append({"messages": messages, "tools": tools, "stream": stream})
        if stream:
            return self._run_streaming()
        return self._run_non_streaming()

    async def _run_non_streaming(self) -> _FakeAgentResponse:
        return _FakeAgentResponse(text="Here is the answer.")

    async def _run_streaming(self):
        for chunk in self.stream_chunks:
            yield _FakeAgentUpdate(text=chunk)


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


async def test_run_filters_function_tools_by_allowed_tool_names_on_tool_context():
    api_client = _FakeApiClient(latest_version="1")
    project_service = _FakeProjectService(api_client=api_client)
    fake_agent = _FakeFoundryAgent(project_client=None, agent_name="", agent_version="")
    tool_registry = AgentToolRegistry()

    async def _call_a(arguments: dict, context: ToolCallContext) -> dict:
        return {"tool": "a"}

    async def _call_b(arguments: dict, context: ToolCallContext) -> dict:
        return {"tool": "b"}

    tool_registry.register(agent_id="genie-orchestrator", tool_name="call_a", fn=_call_a)
    tool_registry.register(agent_id="genie-orchestrator", tool_name="call_b", fn=_call_b)
    agent_definition = AgentDefinition(
        id="genie-orchestrator",
        name="Genie Orchestrator",
        role="mission_orchestration",
        description="Drives the mission.",
        foundry_agent_id="genie-orchestrator",
        tool_definitions=[
            AgentToolDefinition(name="call_a", description="Delegate A."),
            AgentToolDefinition(name="call_b", description="Delegate B."),
        ],
    )
    tool_context = ToolCallContext(
        agent=agent_definition, session_id="s1", trace_id="t1", allowed_tool_names=["call_a"]
    )
    provider = FoundryAgentProvider(
        project_service,
        tool_registry=tool_registry,
        agent_factory=_agent_factory_returning(fake_agent),
    )

    await provider.run(
        foundry_agent_id="genie-orchestrator", input_text="hello", tool_context=tool_context
    )

    [run_call] = fake_agent.run_calls
    [built_tool] = run_call["tools"]
    assert built_tool.name == "call_a"


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


async def test_run_stream_yields_deltas_then_a_final_chunk_with_accumulated_text():
    api_client = _FakeApiClient(latest_version="7")
    project_service = _FakeProjectService(api_client=api_client)
    fake_agent = _FakeFoundryAgent(project_client=None, agent_name="", agent_version="")
    fake_agent.stream_chunks = ["Here ", "is ", "the answer."]
    provider = FoundryAgentProvider(
        project_service, agent_factory=_agent_factory_returning(fake_agent)
    )

    chunks = [
        chunk
        async for chunk in provider.run_stream(foundry_agent_id="requirements-analyst", input_text="hello")
    ]

    deltas = [chunk.delta for chunk in chunks if chunk.delta is not None]
    finals = [chunk.final for chunk in chunks if chunk.final is not None]
    assert deltas == ["Here ", "is ", "the answer."]
    assert len(finals) == 1
    assert finals[0].output_text == "Here is the answer."
    assert finals[0].raw_status == "completed"
    assert fake_agent.run_calls[0]["stream"] is True


async def test_run_stream_raises_foundry_unavailable_when_no_deltas_produced():
    api_client = _FakeApiClient()
    project_service = _FakeProjectService(api_client=api_client)
    fake_agent = _FakeFoundryAgent(project_client=None, agent_name="", agent_version="")
    fake_agent.stream_chunks = []
    provider = FoundryAgentProvider(
        project_service, agent_factory=_agent_factory_returning(fake_agent)
    )

    with pytest.raises(FoundryUnavailableError, match="no output text"):
        async for _ in provider.run_stream(foundry_agent_id="agent-123", input_text="hello"):
            pass


async def test_run_stream_wraps_unexpected_exceptions_as_foundry_unavailable():
    class _BoomProjectService:
        def get_api_client(self):
            raise RuntimeError("credential expired")

        def get_async_project_client(self):  # pragma: no cover - not reached
            raise RuntimeError("credential expired")

    provider = FoundryAgentProvider(_BoomProjectService())

    with pytest.raises(FoundryUnavailableError, match="credential expired"):
        async for _ in provider.run_stream(foundry_agent_id="agent-123", input_text="hello"):
            pass


class _FakeSettings:
    def __init__(self, **values: Any) -> None:
        for key, value in values.items():
            setattr(self, key, value)


def _mcp_agent_definition(mcp_tools: list[AgentMcpToolDefinition]) -> AgentDefinition:
    return AgentDefinition(
        id="finops-hub-agent",
        name="FinOps Hub Agent",
        role="finops",
        description="Queries the FinOps hub.",
        foundry_agent_id="finops-hub-agent",
        mcp_tools=mcp_tools,
    )


def test_build_mcp_tools_skips_tool_when_server_url_not_configured():
    provider = FoundryAgentProvider(_FakeProjectService(api_client=_FakeApiClient()), settings=_FakeSettings())
    mcp_definition = AgentMcpToolDefinition(
        name="azure-mcp-kusto",
        description="Kusto query tool.",
        server_url_setting="finops_hub_mcp_server_url",
    )
    tool_context = ToolCallContext(
        agent=_mcp_agent_definition([mcp_definition]), session_id="s1", trace_id="t1"
    )

    tools = provider._build_mcp_tools(tool_context)

    assert tools == []


def test_build_mcp_tools_builds_tool_without_auth_header_when_client_id_not_configured():
    settings = _FakeSettings(finops_hub_mcp_server_url="https://mcp.example.com")
    provider = FoundryAgentProvider(_FakeProjectService(api_client=_FakeApiClient()), settings=settings)
    mcp_definition = AgentMcpToolDefinition(
        name="azure-mcp-kusto",
        description="Kusto query tool.",
        server_url_setting="finops_hub_mcp_server_url",
        allowed_tools=["azmcp_kusto_query"],
    )
    tool_context = ToolCallContext(
        agent=_mcp_agent_definition([mcp_definition]), session_id="s1", trace_id="t1"
    )

    [tool] = provider._build_mcp_tools(tool_context)

    assert tool.name == "azure-mcp-kusto"
    assert tool.url == "https://mcp.example.com"


def test_build_mcp_tools_attaches_bearer_token_header_when_client_id_configured():
    settings = _FakeSettings(
        finops_hub_mcp_server_url="https://mcp.example.com",
        finops_hub_mcp_client_id="11111111-1111-1111-1111-111111111111",
    )
    project_service = _FakeProjectService(api_client=_FakeApiClient(), mcp_access_token="fake-access-token")
    provider = FoundryAgentProvider(project_service, settings=settings)
    mcp_definition = AgentMcpToolDefinition(
        name="azure-mcp-kusto",
        description="Kusto query tool.",
        server_url_setting="finops_hub_mcp_server_url",
        client_id_setting="finops_hub_mcp_client_id",
    )
    tool_context = ToolCallContext(
        agent=_mcp_agent_definition([mcp_definition]), session_id="s1", trace_id="t1"
    )

    [tool] = provider._build_mcp_tools(tool_context)

    # agent_framework.MCPStreamableHTTPTool only stores header_provider
    # privately (no public accessor - confirmed by reading the installed
    # package's source); its own call_tool() invokes it with the in-flight
    # call's function arguments, and the return value becomes the entire
    # headers dict for that request (no pre-existing headers to merge).
    assert tool._header_provider is not None
    headers = tool._header_provider({"cluster_uri": "https://cluster.example.com"})
    assert headers == {"Authorization": "Bearer fake-access-token"}
    assert project_service.mcp_access_token_requested_for == "11111111-1111-1111-1111-111111111111"


def test_build_mcp_tools_header_provider_raises_foundry_unavailable_on_token_failure():
    settings = _FakeSettings(
        finops_hub_mcp_server_url="https://mcp.example.com",
        finops_hub_mcp_client_id="11111111-1111-1111-1111-111111111111",
    )
    project_service = _FakeProjectService(
        api_client=_FakeApiClient(),
        mcp_access_token_error=FoundryUnavailableError(
            "Failed to acquire a Microsoft Entra ID access token for MCP server "
            "audience 'api://11111111-1111-1111-1111-111111111111': no managed "
            "identity available"
        ),
    )
    provider = FoundryAgentProvider(project_service, settings=settings)
    mcp_definition = AgentMcpToolDefinition(
        name="azure-mcp-kusto",
        description="Kusto query tool.",
        server_url_setting="finops_hub_mcp_server_url",
        client_id_setting="finops_hub_mcp_client_id",
    )
    tool_context = ToolCallContext(
        agent=_mcp_agent_definition([mcp_definition]), session_id="s1", trace_id="t1"
    )

    [tool] = provider._build_mcp_tools(tool_context)

    with pytest.raises(FoundryUnavailableError, match="Microsoft Entra ID access token"):
        tool._header_provider({})

