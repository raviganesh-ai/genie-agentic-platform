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

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any
from uuid import uuid4

from app.models.workflow_models import WorkflowRunResult, WorkflowStepInput
from app.orchestration.workflow_runtime import WorkflowRuntime
from app.repositories.workflow_run_repository import WorkflowRunRepository

__all__ = ["UnknownWorkflowRunError", "WorkflowExecutionService"]

logger = logging.getLogger(__name__)


class UnknownWorkflowRunError(RuntimeError):
    """Raised when resuming a workflow run id that has no recorded history."""


class WorkflowExecutionService:
    """Starts, resumes, and tracks the history of workflow runs for a session."""

    def __init__(self, *, runtime: WorkflowRuntime, repository: WorkflowRunRepository) -> None:
        self._runtime = runtime
        self._repository = repository
        # Strong references to in-flight background runs. asyncio only keeps
        # weak references to tasks, so without this a run could be garbage
        # collected mid-execution.
        self._background_tasks: set[asyncio.Task[None]] = set()

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

    async def start_workflow_background(
        self,
        *,
        workflow_id: str,
        session_id: str,
        trace_id: str,
        step_inputs: dict[str, WorkflowStepInput] | None = None,
        transcript_text: str = "",
        agent_scope_id: str | None = None,
    ) -> WorkflowRunResult:
        """Accepts a run and executes it detached from the caller's request.

        A full mission routinely outlives any HTTP request (individual
        Foundry agent steps have been observed above 340s, well past the
        Azure Container Apps ingress request cap), so holding the connection
        open for the whole run makes the proxy sever it - which the browser
        then surfaces as a misleading CORS error. Instead the run id is
        pre-allocated and a ``running`` snapshot persisted immediately, so
        the caller can track progress via ``GET /runs/{id}`` and the live
        workflow event stream while execution continues server-side.
        """
        workflow_run_id = str(uuid4())
        accepted = WorkflowRunResult(
            workflow_run_id=workflow_run_id,
            workflow_id=workflow_id,
            session_id=session_id,
            status="running",
            agent_scope_id=agent_scope_id,
            detail="Workflow run accepted; executing in the background.",
        )
        await self._repository.put(accepted)
        self._spawn(
            self._runtime.run_workflow(
                workflow_id=workflow_id,
                session_id=session_id,
                trace_id=trace_id,
                step_inputs=step_inputs,
                transcript_text=transcript_text,
                agent_scope_id=agent_scope_id,
                workflow_run_id=workflow_run_id,
                on_progress=self._repository.put,
            ),
            accepted=accepted,
        )
        return accepted

    async def resume_workflow_background(
        self,
        *,
        workflow_run_id: str,
        session_id: str,
        trace_id: str,
        step_inputs: dict[str, WorkflowStepInput] | None = None,
        transcript_text: str = "",
    ) -> WorkflowRunResult:
        """Resumes a paused run detached from the caller's request.

        Same rationale as ``start_workflow_background``.
        """
        previous = await self._repository.get(workflow_run_id=workflow_run_id)
        if previous is None:
            raise UnknownWorkflowRunError(f"No workflow run '{workflow_run_id}' found to resume.")

        accepted = previous.model_copy(
            update={
                "status": "running",
                "detail": "Workflow resume accepted; executing in the background.",
            }
        )
        await self._repository.put(accepted)
        self._spawn(
            self._runtime.run_workflow(
                workflow_id=previous.workflow_id,
                session_id=session_id,
                trace_id=trace_id,
                step_inputs=step_inputs,
                transcript_text=transcript_text,
                resume_from=previous,
                on_progress=self._repository.put,
            ),
            accepted=accepted,
        )
        return accepted

    def _spawn(
        self,
        coro: Coroutine[Any, Any, WorkflowRunResult],
        *,
        accepted: WorkflowRunResult,
    ) -> None:
        task = asyncio.create_task(self._execute_detached(coro, accepted=accepted))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _execute_detached(
        self,
        coro: Coroutine[Any, Any, WorkflowRunResult],
        *,
        accepted: WorkflowRunResult,
    ) -> None:
        """Awaits a detached run and always persists a terminal outcome.

        Nothing is waiting on this task's return value, so an unrecorded
        exception would leave the run stuck at ``running`` forever and the
        UI polling indefinitely. Every failure is therefore persisted as a
        ``failed`` run; the full traceback goes to the server log only.
        """
        try:
            result = await coro
        except Exception as exc:
            logger.exception(
                "Background workflow run %s failed.", accepted.workflow_run_id
            )
            await self._repository.put(
                accepted.model_copy(
                    update={
                        "status": "failed",
                        "detail": f"{type(exc).__name__}: {exc}",
                    }
                )
            )
        else:
            await self._repository.put(result)

    async def list_runs_for_session(self, session_id: str) -> list[WorkflowRunResult]:
        return await self._repository.list_for_session(session_id=session_id)
