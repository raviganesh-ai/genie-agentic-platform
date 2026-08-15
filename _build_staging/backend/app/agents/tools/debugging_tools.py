"""Debugging Agent function tool: ``query_governance_trace``.

Reads the real governance event audit trail for a session
(``GovernanceService.events_for_session``) so the agent's diagnosis is
grounded in what actually happened, rather than only what it infers from
prose.
"""
from __future__ import annotations

from typing import Any

from app.agents.tool_execution import AgentToolRegistry, ToolCallContext, ToolExecutionError
from app.governance.governance_service import GovernanceService

__all__ = ["register_debugging_tools"]

_AGENT_ID = "debugging-agent"
_TOOL_NAME = "query_governance_trace"


def register_debugging_tools(
    registry: AgentToolRegistry, *, governance_service: GovernanceService
) -> None:
    """Register ``query_governance_trace`` for the ``debugging-agent`` agent."""

    async def _query_governance_trace(
        arguments: dict[str, Any], context: ToolCallContext
    ) -> dict[str, Any]:
        if context.session_id is None:
            raise ToolExecutionError(f"'{_TOOL_NAME}' requires an active session_id.")

        category_filter = arguments.get("category")
        events = await governance_service.events_for_session(context.session_id)
        if category_filter:
            events = [event for event in events if event.category == category_filter]

        return {
            "count": len(events),
            "events": [
                {
                    "id": event.id,
                    "category": event.category,
                    "agent_id": event.agent_id,
                    "trace_id": event.trace_id,
                    "detail": event.detail,
                }
                for event in events
            ],
        }

    registry.register(agent_id=_AGENT_ID, tool_name=_TOOL_NAME, fn=_query_governance_trace)
