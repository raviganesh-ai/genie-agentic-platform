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

from app.agents.tools.orchestration_tools import COMPONENT_FAILURE_MARKER
from app.models.workflow_models import WorkflowRunResult, WorkflowStepInput, WorkflowStepResult
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
        # Which workflow_run_ids currently have a background execution still
        # in flight - guards resume_workflow_background against a duplicate
        # resume for the same run (e.g. a user re-clicking Approve after
        # navigating back before the first attempt has settled) racing a
        # second concurrent WorkflowRuntime.run_workflow call against the
        # same run id, whose interleaved on_progress writes would otherwise
        # clobber each other non-deterministically.
        self._in_flight_run_ids: set[str] = set()

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

    async def update_step_output(
        self, *, workflow_run_id: str, step_id: str, output_text: str
    ) -> WorkflowRunResult:
        """Durably persists a new ``output_text`` for one already-recorded
        step result of an existing run (e.g. a single component's code was
        regenerated and spliced back into ``build-solution``'s output -
        see ``AgentOrchestrator.update_build_component``).

        Also re-derives that step's ``status``: ``build-solution`` is
        rejected closed to ``"failed"`` the moment any component's own
        generation fails (see ``workflow_step_executor``'s
        ``_reject_failed_generated_build``), and that failure is recorded
        as a placeholder block carrying ``COMPONENT_FAILURE_MARKER``
        inside the step's own ``output_text`` (see
        ``orchestration_tools._component_failure_piece``). Regenerating the
        failed component fixes that text but, without this, the step's
        ``status`` field stayed permanently ``"failed"`` regardless -
        Deploy & Launch gates on that status, so a mission with even one
        originally-failed component could never reach deployment again no
        matter how many times its broken piece was successfully fixed (a
        real, observed gap found validating this exact path end to end).
        A step only ever flips ``"failed"`` -> ``"completed"`` here, never
        the reverse - a regeneration's own output is never re-validated at
        this layer, so it is still each caller's responsibility (e.g.
        ``WorkshopService.regenerate_component``) never to splice in text
        that still fails.

        Every other field of the matching ``WorkflowStepResult``
        (timestamps, resolved_variables, ...) is preserved unchanged.
        Raises ``UnknownWorkflowRunError`` if the run or the named step
        result does not exist, so a caller can never silently no-op this.
        """

        run = await self._repository.get(workflow_run_id=workflow_run_id)
        if run is None:
            raise UnknownWorkflowRunError(f"No workflow run '{workflow_run_id}' found.")
        if not any(result.step_id == step_id for result in run.step_results):
            raise UnknownWorkflowRunError(
                f"Workflow run '{workflow_run_id}' has no recorded result for step '{step_id}'."
            )

        def _update_for(result: WorkflowStepResult) -> dict[str, Any]:
            fields: dict[str, Any] = {"output_text": output_text}
            if result.status == "failed" and COMPONENT_FAILURE_MARKER not in output_text:
                fields["status"] = "completed"
            return fields

        updated_run = run.model_copy(
            update={
                "step_results": [
                    result.model_copy(update=_update_for(result))
                    if result.step_id == step_id
                    else result
                    for result in run.step_results
                ]
            }
        )
        await self._repository.put(updated_run)
        return updated_run

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

        Same rationale as ``start_workflow_background``. A duplicate resume
        for a run id that already has a background execution in flight is a
        no-op that just returns the current (already ``running``) snapshot,
        rather than spawning a second concurrent ``run_workflow`` call - see
        ``_in_flight_run_ids``.
        """
        previous = await self._repository.get(workflow_run_id=workflow_run_id)
        if previous is None:
            raise UnknownWorkflowRunError(f"No workflow run '{workflow_run_id}' found to resume.")

        if workflow_run_id in self._in_flight_run_ids:
            logger.info(
                "Ignoring duplicate resume for workflow run %s; a prior resume is still executing.",
                workflow_run_id,
            )
            return previous

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
        self._in_flight_run_ids.add(accepted.workflow_run_id)
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
        finally:
            self._in_flight_run_ids.discard(accepted.workflow_run_id)

    async def list_runs_for_session(self, session_id: str) -> list[WorkflowRunResult]:
        return await self._repository.list_for_session(session_id=session_id)
