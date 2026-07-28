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
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Protocol

from agent_framework import FunctionTool
from agent_framework.foundry import FoundryAgent

from app.agents.foundry.errors import FoundryUnavailableError
from app.agents.foundry.project_service import FoundryProjectService
from app.agents.models import AgentToolDefinition
from app.agents.tool_execution import AgentToolRegistry, ToolCallContext, ToolExecutionError

__all__ = ["FoundryAgentClient", "FoundryAgentProvider", "FoundryRunResult"]


@dataclass(frozen=True)
class FoundryRunResult:
    """The outcome of one successful run against a Foundry-hosted agent."""

    output_text: str
    raw_status: str
    latency_ms: float


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


class _RunnableAgent(Protocol):
    """The minimal surface ``FoundryAgentProvider`` needs from a constructed agent.

    Matches ``agent_framework.foundry.FoundryAgent`` (via its base
    ``Agent.run``); isolated as a Protocol so tests can inject a fake
    without any network access.
    """

    async def run(self, messages: Any, *, tools: Any = None) -> Any:
        ...


class FoundryAgentProvider:
    """Runs an existing Foundry Prompt Agent version and returns its reply."""

    def __init__(
        self,
        project_service: FoundryProjectService,
        *,
        tool_registry: AgentToolRegistry | None = None,
        agent_factory: Any = FoundryAgent,
    ) -> None:
        self._project_service = project_service
        self._tool_registry = tool_registry
        # Injectable so unit tests can substitute a fake agent_framework
        # Agent-like object instead of making real network calls.
        self._agent_factory = agent_factory

    async def run(
        self,
        *,
        foundry_agent_id: str,
        input_text: str,
        tool_context: ToolCallContext | None = None,
    ) -> FoundryRunResult:
        started = time.monotonic()
        try:
            pinned_version = tool_context.agent.foundry_agent_version if tool_context else None
            if pinned_version:
                agent_version = pinned_version
            else:
                api_client = self._project_service.get_api_client()
                agent_version = await asyncio.to_thread(api_client.get_latest_version, foundry_agent_id)

            project_client = self._project_service.get_async_project_client()
            tools = self._build_function_tools(tool_context)

            agent: _RunnableAgent = self._agent_factory(
                project_client=project_client,
                agent_name=foundry_agent_id,
                agent_version=agent_version,
            )
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

    def _build_function_tools(self, tool_context: ToolCallContext | None) -> list[FunctionTool]:
        if tool_context is None or self._tool_registry is None:
            return []

        return [
            self._build_one_tool(tool_definition, tool_context)
            for tool_definition in tool_context.agent.tool_definitions
        ]

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
            input_model=_tool_definition_to_json_schema(tool_definition),
        )


def _tool_definition_to_json_schema(tool_definition: AgentToolDefinition) -> dict[str, Any]:
    """Render an ``AgentToolDefinition`` as a JSON-schema object for ``FunctionTool``.

    CONFIRMED via sandbox testing (see session notes): ``agent_framework.
    FunctionTool(input_model=...)`` accepts a plain JSON-schema ``dict``
    directly - no ``pydantic.BaseModel`` is required.
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
