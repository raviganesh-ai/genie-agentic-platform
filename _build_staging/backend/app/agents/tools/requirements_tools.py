"""Requirements Analyst function tool: ``record_requirements``.

Persists the structured requirements/risks/assumptions/constraints the
agent extracts from a transcript into Shared Collaboration Memory (see
"Memory Architecture" in ``.github/copilot-instructions.md``), so
downstream services read a genuine structured record rather than having
to regex-parse the agent's free-form prose.
"""
from __future__ import annotations

from typing import Any

from app.agents.tool_execution import AgentToolRegistry, ToolCallContext, ToolExecutionError
from app.memory.memory_models import MemoryAccessDeniedError
from app.memory.memory_service import MemoryService

__all__ = ["register_requirements_tools"]

_AGENT_ID = "requirements-analyst"
_TOOL_NAME = "record_requirements"


def register_requirements_tools(registry: AgentToolRegistry, *, memory_service: MemoryService) -> None:
    """Register ``record_requirements`` for the ``requirements-analyst`` agent."""

    async def _record_requirements(
        arguments: dict[str, Any], context: ToolCallContext
    ) -> dict[str, Any]:
        if context.session_id is None:
            raise ToolExecutionError(f"'{_TOOL_NAME}' requires an active session_id.")

        items = arguments.get("items")
        if not isinstance(items, list) or not items:
            raise ToolExecutionError(f"'{_TOOL_NAME}' requires a non-empty 'items' list.")

        try:
            record = await memory_service.shared.write(
                agent=context.agent,
                session_id=context.session_id,
                trace_id=context.trace_id,
                key=f"requirements:{context.session_id}",
                classification="requirement",
                content={"items": items},
            )
        except MemoryAccessDeniedError as exc:
            raise ToolExecutionError(str(exc)) from exc

        return {
            "recorded": True,
            "record_id": record.id,
            "version": record.version,
            "item_count": len(items),
        }

    registry.register(agent_id=_AGENT_ID, tool_name=_TOOL_NAME, fn=_record_requirements)
