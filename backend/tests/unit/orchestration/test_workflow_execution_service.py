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

import pytest

from app.models.workflow_models import WorkflowRunResult, WorkflowStepInput, WorkflowStepResult
from app.orchestration.workflow_execution_service import (
    UnknownWorkflowRunError,
    WorkflowExecutionService,
)
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


def _step_result(**overrides: object) -> WorkflowStepResult:
    from datetime import UTC, datetime

    defaults: dict[str, object] = {
        "step_id": "build-solution",
        "agent_id": "build-agent",
        "status": "completed",
        "output_text": "original output",
        "started_at": datetime(2026, 1, 1, tzinfo=UTC),
        "completed_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    defaults.update(overrides)
    return WorkflowStepResult.model_validate(defaults)


async def test_update_step_output_replaces_only_the_named_steps_output_text() -> None:
    """Regression coverage for a real incident: a Workshop "Regenerate"
    action for a single component returned the new code to the caller but
    never persisted it anywhere, so every subsequent Deploy & Launch kept
    re-materializing the original, unmodified build output."""

    repository = InMemoryWorkflowRunRepository()
    await repository.put(
        _run(
            step_results=[
                _step_result(step_id="analyze-requirements", output_text="requirements text"),
                _step_result(step_id="build-solution", output_text="original build output"),
            ]
        )
    )
    service = WorkflowExecutionService(runtime=object(), repository=repository)  # type: ignore[arg-type]

    updated = await service.update_step_output(
        workflow_run_id="run-1",
        step_id="build-solution",
        output_text="regenerated build output",
    )

    build_result = next(r for r in updated.step_results if r.step_id == "build-solution")
    other_result = next(r for r in updated.step_results if r.step_id == "analyze-requirements")
    assert build_result.output_text == "regenerated build output"
    # Every other field of the updated step result is preserved untouched.
    assert build_result.status == "completed"
    assert build_result.agent_id == "build-agent"
    # A different step's result must be left completely untouched.
    assert other_result.output_text == "requirements text"

    # The change must be durably persisted, not just returned.
    persisted = await repository.get(workflow_run_id="run-1")
    assert persisted is not None
    persisted_build_result = next(
        r for r in persisted.step_results if r.step_id == "build-solution"
    )
    assert persisted_build_result.output_text == "regenerated build output"


async def test_update_step_output_raises_for_unknown_run() -> None:
    repository = InMemoryWorkflowRunRepository()
    service = WorkflowExecutionService(runtime=object(), repository=repository)  # type: ignore[arg-type]

    with pytest.raises(UnknownWorkflowRunError):
        await service.update_step_output(
            workflow_run_id="no-such-run", step_id="build-solution", output_text="x"
        )


async def test_update_step_output_raises_for_unknown_step() -> None:
    repository = InMemoryWorkflowRunRepository()
    await repository.put(_run(step_results=[_step_result(step_id="build-solution")]))
    service = WorkflowExecutionService(runtime=object(), repository=repository)  # type: ignore[arg-type]

    with pytest.raises(UnknownWorkflowRunError):
        await service.update_step_output(
            workflow_run_id="run-1", step_id="no-such-step", output_text="x"
        )


async def test_update_step_output_flips_a_failed_step_to_completed_once_the_failure_marker_is_gone() -> None:
    """Regression coverage for a real incident found validating the
    regenerate-persistence fix end to end: build-solution is rejected
    closed to status="failed" the moment any one component's generation
    fails (see workflow_step_executor._reject_failed_generated_build), and
    that failure is recorded as a GENERATION FAILED placeholder inside the
    step's own output_text. Regenerating the failed component via
    WorkshopService.regenerate_component fixes the text, but without this,
    the step's status stayed permanently "failed" regardless - Deploy &
    Launch gates on that status, so the mission could never reach
    deployment again no matter how many times its broken component was
    actually fixed."""

    repository = InMemoryWorkflowRunRepository()
    await repository.put(
        _run(
            step_results=[
                _step_result(
                    step_id="build-solution",
                    status="failed",
                    output_text="```text\n# agent: Landing Page\n# GENERATION FAILED: boom\n```",
                ),
            ]
        )
    )
    service = WorkflowExecutionService(runtime=object(), repository=repository)  # type: ignore[arg-type]

    updated = await service.update_step_output(
        workflow_run_id="run-1",
        step_id="build-solution",
        output_text="```tsx\n// agent: Landing Page\nexport default function Landing() { return null; }\n```",
    )

    build_result = next(r for r in updated.step_results if r.step_id == "build-solution")
    assert build_result.status == "completed"

    persisted = await repository.get(workflow_run_id="run-1")
    assert persisted is not None
    persisted_build_result = next(
        r for r in persisted.step_results if r.step_id == "build-solution"
    )
    assert persisted_build_result.status == "completed"


async def test_update_step_output_leaves_a_failed_step_failed_if_the_marker_is_still_present() -> None:
    """A regeneration that fixed one component but left another one's
    GENERATION FAILED placeholder untouched must not be treated as fully
    healed - the step must stay "failed" until every failure is gone."""

    repository = InMemoryWorkflowRunRepository()
    await repository.put(
        _run(
            step_results=[
                _step_result(
                    step_id="build-solution",
                    status="failed",
                    output_text=(
                        "```text\n# agent: Landing Page\n# GENERATION FAILED: boom\n```\n"
                        "```text\n# agent: Onboarding Wizard\n# GENERATION FAILED: boom\n```"
                    ),
                ),
            ]
        )
    )
    service = WorkflowExecutionService(runtime=object(), repository=repository)  # type: ignore[arg-type]

    updated = await service.update_step_output(
        workflow_run_id="run-1",
        step_id="build-solution",
        output_text=(
            "```tsx\n// agent: Landing Page\nexport default function Landing() { return null; }\n```\n"
            "```text\n# agent: Onboarding Wizard\n# GENERATION FAILED: still broken\n```"
        ),
    )

    build_result = next(r for r in updated.step_results if r.step_id == "build-solution")
    assert build_result.status == "failed"


async def test_update_step_output_never_flips_an_already_completed_step_to_failed() -> None:
    """update_step_output only ever moves "failed" -> "completed"; it must
    never downgrade an already-completed step, since a regeneration's own
    output is validated by its own caller, not re-validated here."""

    repository = InMemoryWorkflowRunRepository()
    await repository.put(
        _run(
            step_results=[
                _step_result(step_id="build-solution", status="completed", output_text="original"),
            ]
        )
    )
    service = WorkflowExecutionService(runtime=object(), repository=repository)  # type: ignore[arg-type]

    updated = await service.update_step_output(
        workflow_run_id="run-1",
        step_id="build-solution",
        output_text="```text\n# agent: X\n# GENERATION FAILED: boom\n```",
    )

    build_result = next(r for r in updated.step_results if r.step_id == "build-solution")
    assert build_result.status == "completed"
