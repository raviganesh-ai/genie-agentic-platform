"""Build Agent function tool: ``record_build_output``.

Persists a structured summary of the generated customer-facing UI and its
dedicated multi-agent workflow design into Shared Collaboration Memory,
rather than only stating them as free-form prose the model must be
trusted to have produced correctly.
"""
from __future__ import annotations

from typing import Any

from app.agents.tool_execution import AgentToolRegistry, ToolCallContext, ToolExecutionError
from app.memory.memory_models import MemoryAccessDeniedError
from app.memory.memory_service import MemoryService

__all__ = ["register_build_tools"]

_AGENT_ID = "build-agent"
_TOOL_NAME = "record_build_output"


def register_build_tools(registry: AgentToolRegistry, *, memory_service: MemoryService) -> None:
    """Register ``record_build_output`` for the ``build-agent`` agent."""

    async def _record_build_output(
        arguments: dict[str, Any], context: ToolCallContext
    ) -> dict[str, Any]:
        if context.session_id is None:
            raise ToolExecutionError(f"'{_TOOL_NAME}' requires an active session_id.")

        ui_summary = arguments.get("ui_summary")
        workflow_summary = arguments.get("workflow_summary")
        if not isinstance(ui_summary, str) or not ui_summary.strip():
            raise ToolExecutionError(f"'{_TOOL_NAME}' requires a non-empty 'ui_summary' string.")
        if not isinstance(workflow_summary, str) or not workflow_summary.strip():
            raise ToolExecutionError(
                f"'{_TOOL_NAME}' requires a non-empty 'workflow_summary' string."
            )

        try:
            record = await memory_service.shared.write(
                agent=context.agent,
                session_id=context.session_id,
                trace_id=context.trace_id,
                key=f"build-output:{context.session_id}",
                classification="roadmap_artifact",
                content={"ui_summary": ui_summary, "workflow_summary": workflow_summary},
            )
        except MemoryAccessDeniedError as exc:
            raise ToolExecutionError(str(exc)) from exc

        return {"recorded": True, "record_id": record.id, "version": record.version}

    registry.register(agent_id=_AGENT_ID, tool_name=_TOOL_NAME, fn=_record_build_output)
