"""Architecture Designer function tools.

``search_reference_architectures`` queries Enterprise Knowledge Memory
(the future Azure AI Search-backed reference architecture/industry pattern
store - see ``app.memory.enterprise_knowledge_store``); ``record_architecture``
persists the agent's resulting design into Shared Collaboration Memory.
Neither tool invents a new store - both are thin bindings onto the
already-existing ``MemoryService`` tiers.
"""
from __future__ import annotations

from typing import Any

from app.agents.tool_execution import AgentToolRegistry, ToolCallContext, ToolExecutionError
from app.memory.memory_models import MemoryAccessDeniedError
from app.memory.memory_service import MemoryService

__all__ = ["register_architecture_tools"]

_AGENT_ID = "architecture-designer"
_DEFAULT_TOP = 5


def register_architecture_tools(registry: AgentToolRegistry, *, memory_service: MemoryService) -> None:
    """Register ``search_reference_architectures``/``record_architecture``."""

    async def _search_reference_architectures(
        arguments: dict[str, Any], context: ToolCallContext
    ) -> dict[str, Any]:
        if context.session_id is None:
            raise ToolExecutionError(
                "'search_reference_architectures' requires an active session_id."
            )
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolExecutionError(
                "'search_reference_architectures' requires a non-empty 'query' string."
            )
        top = arguments.get("top", _DEFAULT_TOP)
        if not isinstance(top, int) or top <= 0:
            top = _DEFAULT_TOP

        try:
            results = await memory_service.enterprise.search(
                agent=context.agent,
                query=query,
                session_id=context.session_id,
                trace_id=context.trace_id,
                top=top,
            )
        except MemoryAccessDeniedError as exc:
            raise ToolExecutionError(str(exc)) from exc

        return {
            "count": len(results),
            "results": [
                {"id": record.id, "classification": record.classification, "content": record.content}
                for record in results
            ],
        }

    async def _record_architecture(
        arguments: dict[str, Any], context: ToolCallContext
    ) -> dict[str, Any]:
        if context.session_id is None:
            raise ToolExecutionError("'record_architecture' requires an active session_id.")
        components = arguments.get("components")
        if not isinstance(components, list) or not components:
            raise ToolExecutionError(
                "'record_architecture' requires a non-empty 'components' list."
            )

        try:
            record = await memory_service.shared.write(
                agent=context.agent,
                session_id=context.session_id,
                trace_id=context.trace_id,
                key=f"architecture:{context.session_id}",
                classification="architecture_finding",
                content={"components": components},
            )
        except MemoryAccessDeniedError as exc:
            raise ToolExecutionError(str(exc)) from exc

        return {
            "recorded": True,
            "record_id": record.id,
            "version": record.version,
            "component_count": len(components),
        }

    registry.register(
        agent_id=_AGENT_ID,
        tool_name="search_reference_architectures",
        fn=_search_reference_architectures,
    )
    registry.register(agent_id=_AGENT_ID, tool_name="record_architecture", fn=_record_architecture)
