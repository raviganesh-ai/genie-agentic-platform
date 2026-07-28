"""Function-tool dispatch for Foundry agent runs.

When an Azure AI Foundry run reaches ``requires_action`` (the model wants
to call one of the agent's registered function tools -
``AgentDefinition.tool_definitions``), ``FoundryAgentProvider`` resolves
the requested tool through an ``AgentToolRegistry`` and awaits its result,
rather than ever fabricating a canned response. This module defines that
dispatch seam; concrete tool implementations live under
``app.agents.tools`` and are bound to real domain services (memory,
governance, customer agent provisioning) at startup - never invented here.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.agents.models import AgentDefinition

__all__ = ["AgentToolRegistry", "ToolCallContext", "ToolExecutionError", "ToolFunction"]


class ToolExecutionError(RuntimeError):
    """Raised when a requested tool is unregistered, misused, or fails to execute."""


@dataclass(frozen=True)
class ToolCallContext:
    """Per-run context threaded from ``AzureAgentGateway`` into a tool call.

    ``agent`` is the calling agent's own ``AgentDefinition`` (used, e.g., to
    satisfy ``MemoryAccessPolicyService`` checks with the caller's real
    identity rather than a synthesized one). ``session_id`` may be ``None``
    for agent executions not tied to any session; tools that require a
    session (nearly all of them) must raise ``ToolExecutionError`` when it
    is missing rather than guessing one.

    ``variables`` are the calling agent's own resolved prompt-template
    variables for this run (``AgentExecutionRequest.variables``) - e.g. for
    a ``genie-orchestrator`` step this is the exact same upstream step
    output/transcript text that was substituted into its own prompt. A
    delegation tool (``app.agents.tools.orchestration_tools``) should treat
    this as the authoritative value for any tool-call argument name that
    matches one of these keys, rather than trusting the model to have
    copied a (possibly large) text block verbatim into its function-call
    arguments.
    """

    agent: AgentDefinition
    session_id: str | None
    trace_id: str
    allowed_tool_names: list[str] | None = None
    variables: dict[str, str] = field(default_factory=dict)


ToolFunction = Callable[[dict[str, Any], ToolCallContext], Awaitable[dict[str, Any]]]


class AgentToolRegistry:
    """Maps ``(agent_id, tool_name)`` to an executable async tool function."""

    def __init__(self) -> None:
        self._tools: dict[tuple[str, str], ToolFunction] = {}

    def register(self, *, agent_id: str, tool_name: str, fn: ToolFunction) -> None:
        self._tools[(agent_id, tool_name)] = fn

    def has_tool(self, *, agent_id: str, tool_name: str) -> bool:
        return (agent_id, tool_name) in self._tools

    async def execute(
        self,
        *,
        agent_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolCallContext,
    ) -> dict[str, Any]:
        fn = self._tools.get((agent_id, tool_name))
        if fn is None:
            raise ToolExecutionError(
                f"No tool '{tool_name}' is registered for agent '{agent_id}'."
            )
        return await fn(arguments, context)
