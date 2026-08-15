"""Workflow execution service.

Tracks the run history for every workflow execution (``WorkflowRunResult``)
started through ``WorkflowRuntime``, and provides the resume-by-id
convenience used when a run pauses at ``waiting_for_approval``. Contains no
execution logic of its own - it only records and looks up prior results and
delegates every actual run/resume to ``WorkflowRuntime``.

Every run/resume call is backed by a ``WorkflowRunRepository`` (in-memory by
default; a real durable backend in production, per the Architecture
Principles in ``.github/copilot-instructions.md``) rather than a bare
in-process dict, and ``WorkflowRuntime.run_workflow``'s ``on_progress`` hook
persists each wave's result as soon as it lands - not just once the whole
(potentially multi-minute, multi-wave) call finally returns. This is what
makes an individual stage recoverable: if the caller that was supposed to
kick off the next step never reaches the server, or the process restarts
mid-run, the last durably persisted stage is never lost.
"""
from __future__ import annotations

from app.models.workflow_models import WorkflowRunResult, WorkflowStepInput
from app.orchestration.workflow_runtime import WorkflowRuntime
from app.repositories.workflow_run_repository import WorkflowRunRepository

__all__ = ["UnknownWorkflowRunError", "WorkflowExecutionService"]


class UnknownWorkflowRunError(RuntimeError):
    """Raised when resuming a workflow run id that has no recorded history."""


class WorkflowExecutionService:
    """Starts, resumes, and tracks the history of workflow runs for a session."""

    def __init__(self, *, runtime: WorkflowRuntime, repository: WorkflowRunRepository) -> None:
        self._runtime = runtime
        self._repository = repository

    async def start_workflow(
        self,
        *,
        workflow_id: str,
        session_id: str,
        trace_id: str,
        step_inputs: dict[str, WorkflowStepInput] | None = None,
        transcript_text: str = "",
        agent_scope_id: str | None = None,
    ) -> WorkflowRunResult:
        result = await self._runtime.run_workflow(
            workflow_id=workflow_id,
            session_id=session_id,
            trace_id=trace_id,
            step_inputs=step_inputs,
            transcript_text=transcript_text,
            agent_scope_id=agent_scope_id,
            on_progress=self._repository.put,
        )
        await self._repository.put(result)
        return result

    async def resume_workflow(
        self,
        *,
        workflow_run_id: str,
        session_id: str,
        trace_id: str,
        step_inputs: dict[str, WorkflowStepInput] | None = None,
        transcript_text: str = "",
    ) -> WorkflowRunResult:
        previous = await self._repository.get(workflow_run_id=workflow_run_id)
        if previous is None:
            raise UnknownWorkflowRunError(f"No workflow run '{workflow_run_id}' found to resume.")

        result = await self._runtime.run_workflow(
            workflow_id=previous.workflow_id,
            session_id=session_id,
            trace_id=trace_id,
            step_inputs=step_inputs,
            transcript_text=transcript_text,
            resume_from=previous,
            on_progress=self._repository.put,
        )
        await self._repository.put(result)
        return result

    async def get_run(self, workflow_run_id: str) -> WorkflowRunResult | None:
        return await self._repository.get(workflow_run_id=workflow_run_id)

    async def list_runs_for_session(self, session_id: str) -> list[WorkflowRunResult]:
        return await self._repository.list_for_session(session_id=session_id)
