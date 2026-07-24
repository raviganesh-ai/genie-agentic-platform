"""Workflow execution service.

Tracks the run history for every workflow execution (``WorkflowRunResult``)
started through ``WorkflowRuntime``, and provides the resume-by-id
convenience used when a run pauses at ``waiting_for_approval``. Contains no
execution logic of its own - it only records and looks up prior results and
delegates every actual run/resume to ``WorkflowRuntime``.
"""
from __future__ import annotations

from app.models.workflow_models import WorkflowRunResult, WorkflowStepInput
from app.orchestration.workflow_runtime import WorkflowRuntime

__all__ = ["UnknownWorkflowRunError", "WorkflowExecutionService"]


class UnknownWorkflowRunError(RuntimeError):
    """Raised when resuming a workflow run id that has no recorded history."""


class WorkflowExecutionService:
    """Starts, resumes, and tracks the history of workflow runs for a session."""

    def __init__(self, *, runtime: WorkflowRuntime) -> None:
        self._runtime = runtime
        self._runs: dict[str, WorkflowRunResult] = {}
        self._runs_by_session: dict[str, list[str]] = {}

    async def start_workflow(
        self,
        *,
        workflow_id: str,
        session_id: str,
        trace_id: str,
        step_inputs: dict[str, WorkflowStepInput] | None = None,
        transcript_text: str = "",
    ) -> WorkflowRunResult:
        result = await self._runtime.run_workflow(
            workflow_id=workflow_id,
            session_id=session_id,
            trace_id=trace_id,
            step_inputs=step_inputs,
            transcript_text=transcript_text,
        )
        self._store(result, session_id)
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
        previous = self._runs.get(workflow_run_id)
        if previous is None:
            raise UnknownWorkflowRunError(f"No workflow run '{workflow_run_id}' found to resume.")

        result = await self._runtime.run_workflow(
            workflow_id=previous.workflow_id,
            session_id=session_id,
            trace_id=trace_id,
            step_inputs=step_inputs,
            transcript_text=transcript_text,
            resume_from=previous,
        )
        self._store(result, session_id)
        return result

    def get_run(self, workflow_run_id: str) -> WorkflowRunResult | None:
        return self._runs.get(workflow_run_id)

    def list_runs_for_session(self, session_id: str) -> list[WorkflowRunResult]:
        return [self._runs[run_id] for run_id in self._runs_by_session.get(session_id, [])]

    def _store(self, result: WorkflowRunResult, session_id: str) -> None:
        self._runs[result.workflow_run_id] = result
        run_ids = self._runs_by_session.setdefault(session_id, [])
        if result.workflow_run_id not in run_ids:
            run_ids.append(result.workflow_run_id)
