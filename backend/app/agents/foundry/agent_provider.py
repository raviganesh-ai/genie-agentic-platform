"""Executes a run of an existing, independently deployed Foundry Prompt Agent.

``FoundryAgentProvider`` is the sole implementation of the
``FoundryAgentClient`` protocol that ``AzureAgentGateway`` depends on. It
never creates or defines an agent - every Genie business agent (discovery,
requirements, industry-expert, solution-architect, risk-compliance,
governance, debugging agents, etc.) is provisioned independently in Azure
AI Foundry as a versioned Prompt Agent and referenced here only by its
``foundry_agent_id`` (the Foundry ``agent_name``). This module contains no
agent reasoning, prompts, or business logic of its own.

Execution is delegated entirely to ``agent_framework.foundry.FoundryAgent``
(Microsoft Agent Framework), which owns the full request/response and
function-tool-calling loop internally - there is no manual thread/run
polling or ``requires_action``/``submit_tool_outputs`` handling here
anymore (contrast with the classic Assistants-API pattern this module
used before the Agent Framework migration). When the model calls one of
the agent's registered function tools (``AgentDefinition.tool_definitions``),
Agent Framework invokes the corresponding ``agent_framework.FunctionTool``
built by ``_build_function_tools`` below, which dispatches through an
injected ``AgentToolRegistry`` (never inventing a canned response). If no
registry/context is available to fulfill a tool call, the tool raises
``FoundryUnavailableError`` so the run fails closed rather than guessing.

An agent may also declare ``AgentDefinition.mcp_tools`` - remote MCP
(Model Context Protocol) servers ``agent_framework.MCPStreamableHTTPTool``
connects to directly (e.g. a self-hosted Azure MCP Server exposing Kusto
query tools for a FinOps hub). These are built by ``_build_mcp_tools``
below and, unlike function tools, are never dispatched through
``AgentToolRegistry`` - agent_framework calls the remote server itself.
CONFIRMED via introspecting the installed ``agent_framework_foundry``
package: because every Genie agent references an existing Foundry
resource by name, ALL tool declarations (function *and* MCP-derived) are
stripped from the outbound request and used only for client-side dispatch
matching by name - the model only learns a tool exists from what
``scripts/provision_foundry_agents.py`` already persisted on the Foundry
agent resource. See ``/memories/repo/mcp-tool-integration.md`` for the
full verified rationale.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from agent_framework import FunctionTool, MCPStreamableHTTPTool
from agent_framework.foundry import FoundryAgent

from app.agents.foundry.errors import FoundryUnavailableError
from app.agents.foundry.project_service import FoundryProjectService
from app.agents.models import AgentToolDefinition
from app.agents.tool_execution import AgentToolRegistry, ToolCallContext, ToolExecutionError

__all__ = [
    "FoundryAgentClient",
    "FoundryAgentProvider",
    "FoundryRunResult",
    "FoundryStreamChunk",
    "tool_definition_to_json_schema",
]


@dataclass(frozen=True)
class FoundryRunResult:
    """The outcome of one successful run against a Foundry-hosted agent."""

    output_text: str
    raw_status: str
    latency_ms: float


@dataclass(frozen=True)
class FoundryStreamChunk:
    """One item yielded by ``FoundryAgentClient.run_stream``.

    Every chunk except the last carries a non-empty ``delta`` (an
    incremental slice of the agent's response text as the model produces
    it) and ``final=None``. The last chunk carries ``delta=None`` and a
    populated ``final`` with the same shape ``run()`` returns - so callers
    that only want the finished result can simply consume the stream and
    keep whichever chunk has ``final`` set.
    """

    delta: str | None = None
    final: FoundryRunResult | None = None


class FoundryAgentClient(Protocol):
    """The surface ``AzureAgentGateway`` needs from a Foundry-backed executor.

    ``AzureAgentGateway`` depends only on this protocol, never on the
    azure-ai-projects/agent-framework SDKs or on
    ``FoundryProjectService``/``AgentApiClient`` directly, so the Foundry
    access layer can evolve independently.
    """

    async def run(
        self,
        *,
        foundry_agent_id: str,
        input_text: str,
        tool_context: ToolCallContext | None = None,
    ) -> FoundryRunResult:
        ...

    def run_stream(
        self,
        *,
        foundry_agent_id: str,
        input_text: str,
        tool_context: ToolCallContext | None = None,
    ) -> AsyncIterator[FoundryStreamChunk]:
        ...


class _RunnableAgent(Protocol):
    """The minimal surface ``FoundryAgentProvider`` needs from a constructed agent.

    Matches ``agent_framework.foundry.FoundryAgent`` (via its base
    ``Agent.run``); isolated as a Protocol so tests can inject a fake
    without any network access.
    """

    async def run(self, messages: Any, *, tools: Any = None, stream: bool = False) -> Any:
        ...


class FoundryAgentProvider:
    """Runs an existing Foundry Prompt Agent version and returns its reply."""

    def __init__(
        self,
        project_service: FoundryProjectService,
        *,
        tool_registry: AgentToolRegistry | None = None,
        agent_factory: Any = FoundryAgent,
        settings: Any = None,
    ) -> None:
        self._project_service = project_service
        self._tool_registry = tool_registry
        # Injectable so unit tests can substitute a fake agent_framework
        # Agent-like object instead of making real network calls.
        self._agent_factory = agent_factory
        # Used exclusively to resolve AgentMcpToolDefinition.server_url_setting
        # (e.g. Settings.finops_hub_mcp_server_url) at run time - never to
        # read any other configuration. Loosely typed (Any) rather than
        # importing app.config.settings.Settings to avoid a layering
        # dependency from the Foundry access layer onto app-level config;
        # any object exposing the named attribute works (see tests).
        self._settings = settings

    async def run(
        self,
        *,
        foundry_agent_id: str,
        input_text: str,
        tool_context: ToolCallContext | None = None,
    ) -> FoundryRunResult:
        started = time.monotonic()
        try:
            agent, agent_version, tools = await self._build_runnable_agent(
                foundry_agent_id=foundry_agent_id, tool_context=tool_context
            )
            async with contextlib.AsyncExitStack() as stack:
                await self._connect_mcp_tools(tools, stack)
                response = await agent.run(input_text, tools=tools or None)
            output_text = (getattr(response, "text", None) or "").strip()
            if not output_text:
                raise FoundryUnavailableError(
                    f"Azure AI Foundry run produced no output text for agent "
                    f"'{foundry_agent_id}' (version '{agent_version}')."
                )
        except FoundryUnavailableError:
            raise
        except Exception as exc:
            raise FoundryUnavailableError(
                f"Azure AI Foundry execution failed for agent "
                f"'{foundry_agent_id}': {exc}"
            ) from exc

        latency_ms = (time.monotonic() - started) * 1000
        return FoundryRunResult(output_text=output_text, raw_status="completed", latency_ms=latency_ms)

    async def run_stream(
        self,
        *,
        foundry_agent_id: str,
        input_text: str,
        tool_context: ToolCallContext | None = None,
    ) -> AsyncIterator[FoundryStreamChunk]:
        """Streams incremental response text as the model produces it.

        Yields one ``FoundryStreamChunk(delta=...)`` per incremental text
        update from ``agent_framework``'s streaming run, then a final
        ``FoundryStreamChunk(final=...)`` carrying the same
        ``FoundryRunResult`` shape ``run()`` returns (built from the fully
        accumulated text) once the stream is exhausted. Raises
        ``FoundryUnavailableError`` (never falls back to a canned response)
        on any failure, exactly like ``run()``.
        """

        started = time.monotonic()
        accumulated = ""
        try:
            agent, agent_version, tools = await self._build_runnable_agent(
                foundry_agent_id=foundry_agent_id, tool_context=tool_context
            )
            async with contextlib.AsyncExitStack() as stack:
                await self._connect_mcp_tools(tools, stack)
                stream = agent.run(input_text, tools=tools or None, stream=True)
                async for update in stream:
                    delta = getattr(update, "text", None) or ""
                    if not delta:
                        continue
                    accumulated += delta
                    yield FoundryStreamChunk(delta=delta)

            output_text = accumulated.strip()
            if not output_text:
                raise FoundryUnavailableError(
                    f"Azure AI Foundry run produced no output text for agent "
                    f"'{foundry_agent_id}' (version '{agent_version}')."
                )
        except FoundryUnavailableError:
            raise
        except Exception as exc:
            raise FoundryUnavailableError(
                f"Azure AI Foundry execution failed for agent "
                f"'{foundry_agent_id}': {exc}"
            ) from exc

        latency_ms = (time.monotonic() - started) * 1000
        yield FoundryStreamChunk(
            final=FoundryRunResult(output_text=output_text, raw_status="completed", latency_ms=latency_ms)
        )

    @staticmethod
    async def _connect_mcp_tools(
        tools: list[FunctionTool | MCPStreamableHTTPTool], stack: contextlib.AsyncExitStack
    ) -> None:
        """Opens each MCP tool's remote connection for the lifetime of one run.

        Matches the documented ``agent_framework`` usage pattern (``async
        with mcp_tool: ... agent.run(tools=[mcp_tool])``) explicitly, rather
        than relying on agent_framework's own best-effort auto-connect
        (which only closes when the *agent* object itself is exited as a
        context manager - ``FoundryAgentProvider`` builds a fresh agent per
        run and never does that, so relying on it would leak the
        connection). ``stack`` closes every entered tool once the caller's
        ``async with`` block exits, even if the run raises.
        """

        for tool in tools:
            if isinstance(tool, MCPStreamableHTTPTool):
                await stack.enter_async_context(tool)

    async def _build_runnable_agent(
        self, *, foundry_agent_id: str, tool_context: ToolCallContext | None
    ) -> tuple[_RunnableAgent, str, list[FunctionTool | MCPStreamableHTTPTool]]:
        pinned_version = tool_context.agent.foundry_agent_version if tool_context else None
        if pinned_version:
            agent_version = pinned_version
        else:
            api_client = self._project_service.get_api_client()
            agent_version = await asyncio.to_thread(api_client.get_latest_version, foundry_agent_id)

        project_client = self._project_service.get_async_project_client()
        tools: list[FunctionTool | MCPStreamableHTTPTool] = [
            *self._build_function_tools(tool_context),
            *self._build_mcp_tools(tool_context),
        ]

        agent: _RunnableAgent = self._agent_factory(
            project_client=project_client,
            agent_name=foundry_agent_id,
            agent_version=agent_version,
            # Without this, agent_framework's own function-tool-calling loop
            # (``agent_framework._tools.invoke_function_call``) silently
            # swallows ANY exception a registered ``FunctionTool`` raises
            # (including our own ``FoundryUnavailableError``/``ToolExecutionError``
            # re-raises - see ``_build_one_tool`` below) and feeds the model
            # back only the generic literal string "Error: Function failed."
            # - never the real cause. The model then often just echoes that
            # generic text as its own final answer, which gets stored as the
            # step's whole output (see session notes: this is the exact
            # origin of the "Error: Function failed." text observed wrapped
            # in the Workshop page's generic "Multi-Agent Workflow Design"
            # fallback card). Setting this makes the real exception detail
            # flow back to the model (and thus into the persisted step
            # output) instead of a black-box message.
            function_invocation_configuration={"include_detailed_errors": True},
        )
        return agent, agent_version, tools

    def _build_function_tools(self, tool_context: ToolCallContext | None) -> list[FunctionTool]:
        if tool_context is None or self._tool_registry is None:
            return []

        allowed = tool_context.allowed_tool_names
        return [
            self._build_one_tool(tool_definition, tool_context)
            for tool_definition in tool_context.agent.tool_definitions
            if allowed is None or tool_definition.name in allowed
        ]

    def _build_mcp_tools(self, tool_context: ToolCallContext | None) -> list[MCPStreamableHTTPTool]:
        if tool_context is None:
            return []

        allowed = tool_context.allowed_tool_names
        tools: list[MCPStreamableHTTPTool] = []
        for mcp_definition in tool_context.agent.mcp_tools:
            if allowed is not None and mcp_definition.name not in allowed:
                continue
            server_url = getattr(self._settings, mcp_definition.server_url_setting, None)
            if not server_url:
                # Never fabricate a connection to an unconfigured server -
                # fail this one tool closed (the model simply won't have it
                # available) rather than the whole run.
                continue
            header_provider = self._build_mcp_header_provider(mcp_definition)
            tools.append(
                MCPStreamableHTTPTool(
                    name=mcp_definition.name,
                    url=server_url,
                    description=mcp_definition.description,
                    allowed_tools=mcp_definition.allowed_tools,
                    approval_mode=mcp_definition.approval_mode,
                    header_provider=header_provider,
                )
            )
        return tools

    def _build_mcp_header_provider(
        self, mcp_definition: Any
    ) -> Callable[[dict[str, Any]], dict[str, str]] | None:
        """Builds the ``Authorization: Bearer`` header for a secured MCP server.

        Self-hosted Azure MCP Server deployments enforce Microsoft Entra ID
        authentication on every incoming HTTP request by default (verified
        against Microsoft's own reference deployment,
        Azure-Samples/azmcp-foundry-aca-mi - see
        ``infra/modules/finops-mcp-server.bicep``); disabling that check
        (``--dangerously-disable-http-incoming-auth``) is never done here.
        Returns ``None`` (no header attached) when ``client_id_setting`` is
        unset, for MCP servers that are not Genie-managed secured
        deployments.

        The returned callable matches ``agent_framework.MCPStreamableHTTPTool``'s
        own documented ``header_provider`` contract (confirmed by reading
        the installed package's source): it receives the in-flight tool
        call's own function arguments (``FunctionInvocationContext.kwargs``)
        - not any pre-existing headers - and its return value becomes the
        *entire* headers dict attached to that one outbound MCP request, so
        no merging with prior headers is needed or possible here. Token
        acquisition itself is delegated to
        ``FoundryProjectService.get_mcp_access_token`` rather than importing
        ``azure.identity`` here, since only that module and ``api_client.py``
        are permitted to import Azure SDK packages directly (see
        ``tests/unit/test_architecture_boundary.py``).
        """

        client_id_setting = getattr(mcp_definition, "client_id_setting", None)
        if not client_id_setting:
            return None
        client_id = getattr(self._settings, client_id_setting, None)
        if not client_id:
            return None

        def _provide_headers(call_arguments: dict[str, Any]) -> dict[str, str]:
            token = self._project_service.get_mcp_access_token(client_id)
            return {"Authorization": f"Bearer {token}"}

        return _provide_headers


    def _build_one_tool(
        self, tool_definition: AgentToolDefinition, tool_context: ToolCallContext
    ) -> FunctionTool:
        tool_registry = self._tool_registry
        assert tool_registry is not None  # narrowed by _build_function_tools's caller
        agent_id = tool_context.agent.id
        tool_name = tool_definition.name

        async def _invoke(**kwargs: Any) -> dict[str, Any]:
            try:
                return await tool_registry.execute(
                    agent_id=agent_id,
                    tool_name=tool_name,
                    arguments=kwargs,
                    context=tool_context,
                )
            except ToolExecutionError as exc:
                raise FoundryUnavailableError(
                    f"Tool '{tool_name}' failed for agent '{agent_id}': {exc}"
                ) from exc

        return FunctionTool(
            name=tool_name,
            description=tool_definition.description,
            func=_invoke,
            input_model=tool_definition_to_json_schema(tool_definition),
        )


def tool_definition_to_json_schema(tool_definition: AgentToolDefinition) -> dict[str, Any]:
    """Render an ``AgentToolDefinition`` as a JSON-schema object for ``FunctionTool``.

    CONFIRMED via sandbox testing (see session notes): ``agent_framework.
    FunctionTool(input_model=...)`` accepts a plain JSON-schema ``dict``
    directly - no ``pydantic.BaseModel`` is required. Also reused by
    ``scripts/provision_foundry_agents.py`` to build the *persisted*
    ``azure.ai.projects.models.FunctionTool`` schema on the Foundry agent
    definition itself - required for the model to even know a tool exists
    (see that script's module docstring for why per-request tool
    declarations are silently dropped when an agent is referenced by name).
    """

    properties: dict[str, Any] = {}
    required: list[str] = []
    for parameter in tool_definition.parameters:
        properties[parameter.name] = {
            "type": parameter.type,
            "description": parameter.description,
        }
        if parameter.required:
            required.append(parameter.name)

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema
