"""Unit tests for WorkflowExecutionService's background resume guard.

Regression coverage for a real incident: a user re-clicking "Approve
Architecture & Generate Code" (e.g. after navigating back before the first
attempt's fire-and-forget resume call had settled) sent two ``POST
.../resume`` requests for the same workflow_run_id ~70-100s apart. Each one
independently passed the completed-step re-execution escape hatch in
``WorkflowRuntime.run_workflow`` (a step named in ``step_inputs`` always
re-runs), spawning two concurrent executions of the same step against the
same run id whose interleaved ``on_progress`` writes raced each other,
leaving the run stuck instead of ever completing cleanly.
"""
from __future__ import annotations

import asyncio

from app.models.workflow_models import WorkflowRunResult, WorkflowStepInput
from app.orchestration.workflow_execution_service import WorkflowExecutionService
from app.repositories.workflow_run_repository import InMemoryWorkflowRunRepository


def _run(**overrides: object) -> WorkflowRunResult:
    defaults: dict[str, object] = {
        "workflow_run_id": "run-1",
        "workflow_id": "solution-discovery-workflow",
        "session_id": "session-1",
        "status": "waiting_for_proceed",
    }
    defaults.update(overrides)
    return WorkflowRunResult.model_validate(defaults)


class _SlowRuntime:
    """Stands in for WorkflowRuntime: blocks until released, then completes."""

    def __init__(self) -> None:
        self.call_count = 0
        self.release = asyncio.Event()
        self.entered = asyncio.Event()

    async def run_workflow(self, **kwargs: object) -> WorkflowRunResult:
        self.call_count += 1
        self.entered.set()
        await self.release.wait()
        previous = kwargs["resume_from"]
        assert isinstance(previous, WorkflowRunResult)
        return previous.model_copy(update={"status": "completed"})


async def test_duplicate_resume_for_same_run_is_ignored_while_first_is_in_flight() -> None:
    repository = InMemoryWorkflowRunRepository()
    await repository.put(_run())
    runtime = _SlowRuntime()
    service = WorkflowExecutionService(runtime=runtime, repository=repository)  # type: ignore[arg-type]

    first = await service.resume_workflow_background(
        workflow_run_id="run-1",
        session_id="session-1",
        trace_id="trace-1",
        step_inputs={"build-solution": WorkflowStepInput(step_id="build-solution")},
    )
    await asyncio.wait_for(runtime.entered.wait(), timeout=1.0)

    second = await service.resume_workflow_background(
        workflow_run_id="run-1",
        session_id="session-1",
        trace_id="trace-2",
        step_inputs={"build-solution": WorkflowStepInput(step_id="build-solution")},
    )

    assert first.status == "running"
    assert second.status == "running"
    # Only the first resume actually spawned an execution - the duplicate
    # must not have started a second concurrent run_workflow call.
    assert runtime.call_count == 1

    runtime.release.set()
    await asyncio.sleep(0)  # let the background task finish and persist
    for _ in range(50):
        settled = await service.get_run("run-1")
        if settled is not None and settled.status == "completed":
            break
        await asyncio.sleep(0.01)
    settled = await service.get_run("run-1")
    assert settled is not None
    assert settled.status == "completed"


async def test_resume_after_prior_one_settled_is_allowed_to_run_again() -> None:
    repository = InMemoryWorkflowRunRepository()
    await repository.put(_run())
    runtime = _SlowRuntime()
    service = WorkflowExecutionService(runtime=runtime, repository=repository)  # type: ignore[arg-type]

    await service.resume_workflow_background(
        workflow_run_id="run-1",
        session_id="session-1",
        trace_id="trace-1",
    )
    await asyncio.wait_for(runtime.entered.wait(), timeout=1.0)
    runtime.release.set()
    for _ in range(50):
        settled = await service.get_run("run-1")
        if settled is not None and settled.status == "completed":
            break
        await asyncio.sleep(0.01)

    runtime.entered.clear()
    runtime.release.clear()
    await service.resume_workflow_background(
        workflow_run_id="run-1",
        session_id="session-1",
        trace_id="trace-2",
    )
    await asyncio.wait_for(runtime.entered.wait(), timeout=1.0)

    assert runtime.call_count == 2
