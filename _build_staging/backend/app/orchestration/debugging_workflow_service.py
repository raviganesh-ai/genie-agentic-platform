"""Debugging workflow service.

Implements "DEBUGGING WORKFLOW" for Phase 6: on FailureDetected, routes the
failure to the configured debugging workflow (``Settings.debugging_workflow_id``)
and executes it through the exact same ``WorkflowRuntime`` /
``AzureAgentGateway`` path as every other workflow - automatically
inheriting fail-closed and governance guarantees with no bespoke execution
path. Debugging agents are Azure-hosted; this service never executes
anything locally in production.
"""
from __future__ import annotations

from uuid import uuid4

from app.config.settings import Settings
from app.models.workflow_models import WorkflowRunResult, WorkflowStepInput
from app.orchestration.workflow_execution_service import WorkflowExecutionService

__all__ = ["DebuggingWorkflowService"]


class DebuggingWorkflowService:
    """Routes detected failures into the configured debugging workflow."""

    def __init__(
        self, *, settings: Settings, execution_service: WorkflowExecutionService
    ) -> None:
        self._settings = settings
        self._execution_service = execution_service

    async def handle_failure(
        self,
        *,
        session_id: str,
        source_agent_id: str,
        error: str,
        trace_id: str | None = None,
    ) -> WorkflowRunResult:
        resolved_trace_id = trace_id or str(uuid4())
        step_inputs = {
            "diagnose-failure": WorkflowStepInput(
                step_id="diagnose-failure",
                variables={
                    "failure_details": (
                        f"source_agent_id={source_agent_id}; session_id={session_id}; "
                        f"error={error}"
                    )
                },
            )
        }
        return await self._execution_service.start_workflow(
            workflow_id=self._settings.debugging_workflow_id,
            session_id=session_id,
            trace_id=resolved_trace_id,
            step_inputs=step_inputs,
        )
