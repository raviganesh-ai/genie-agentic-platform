"""Integration tests for WorkflowRuntime's automatic per-step retries.

A step that fails with a retryable ``FoundryUnavailableError`` must be
automatically retried a few times (see workflow_runtime._MAX_STEP_RETRIES)
before being surfaced as a genuinely "failed" step - which remains eligible
for the existing MANUAL retry path (a caller resuming this same
``workflow_run_id`` via ``resume_workflow``).
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.agents.foundry.errors import FoundryUnavailableError
from app.governance.decision_graph_service import DecisionGraphService
from app.governance.governance_service import create_governance_service
from app.models.workflow_models import WorkflowStepResult
from app.orchestration.collaboration_service import CollaborationService
from app.orchestration.handoff_service import HandoffService
from app.orchestration.workflow_checkpoint_service import WorkflowCheckpointService
from app.orchestration.workflow_runtime import _MAX_STEP_RETRIES, WorkflowRuntime
from app.workflows.models import WorkflowDefinition, WorkflowStep
from app.workflows.registry import WorkflowRegistry

from ._orchestration_helpers import build_orchestration_settings


class _FlakyStepExecutor:
    """Fakes ``WorkflowStepExecutor``: fails a given step a fixed number of
    times with a retryable ``FoundryUnavailableError``, then succeeds."""

    def __init__(self, *, fail_times: dict[str, int]) -> None:
        self._remaining_failures = dict(fail_times)
        self.call_counts: dict[str, int] = {}

    async def execute_step(self, *, step: WorkflowStep, **_: Any) -> WorkflowStepResult:
        self.call_counts[step.id] = self.call_counts.get(step.id, 0) + 1
        remaining = self._remaining_failures.get(step.id, 0)
        if remaining > 0:
            self._remaining_failures[step.id] = remaining - 1
            raise FoundryUnavailableError(f"transient failure for step '{step.id}'.")
        now = datetime.now(UTC)
        return WorkflowStepResult(
            step_id=step.id,
            agent_id=step.agent_id,
            status="completed",
            output_text=f"output for {step.id}",
            started_at=now,
            completed_at=now,
        )


def _build_runtime(tmp_path: Path, step_executor: _FlakyStepExecutor) -> WorkflowRuntime:
    settings = build_orchestration_settings(tmp_path / "config")
    governance_service = create_governance_service(settings=settings)
    workflow = WorkflowDefinition(
        id="single-step-workflow",
        name="Single Step Workflow",
        description="One step, no dependencies - used to test automatic step retries.",
        steps=[
            WorkflowStep(
                id="only-step",
                agent_id="agent-a",
                description="The only step.",
                depends_on=[],
                prompt_id="prompt-a",
            )
        ],
    )
    workflow_registry = WorkflowRegistry({"single-step-workflow": workflow})
    return WorkflowRuntime(
        workflow_registry=workflow_registry,
        step_executor=step_executor,  # type: ignore[arg-type]
        handoff_service=HandoffService(governance_service=governance_service),
        collaboration_service=CollaborationService(decision_graph_service=DecisionGraphService()),
        checkpoint_service=WorkflowCheckpointService(),
        approval_service=None,
    )


async def test_step_succeeds_after_transient_failures_within_retry_budget(
    tmp_path: Path,
) -> None:
    """A step failing fewer times than the retry budget must still complete."""

    executor = _FlakyStepExecutor(fail_times={"only-step": _MAX_STEP_RETRIES})
    runtime = _build_runtime(tmp_path, executor)

    result = await runtime.run_workflow(
        workflow_id="single-step-workflow", session_id="session-1", trace_id="trace-1"
    )

    assert result.status == "completed"
    assert result.step_results[0].status == "completed"
    # 1 original attempt + _MAX_STEP_RETRIES automatic retries, the last of
    # which finally succeeds.
    assert executor.call_counts["only-step"] == _MAX_STEP_RETRIES + 1


async def test_step_fails_after_exhausting_the_automatic_retry_budget(
    tmp_path: Path,
) -> None:
    """A step that never stops failing exhausts every automatic retry, then
    surfaces as a genuinely failed run - eligible for the existing manual
    resume_workflow retry path."""

    executor = _FlakyStepExecutor(fail_times={"only-step": _MAX_STEP_RETRIES + 5})
    runtime = _build_runtime(tmp_path, executor)

    result = await runtime.run_workflow(
        workflow_id="single-step-workflow", session_id="session-1", trace_id="trace-1"
    )

    assert result.status == "failed"
    assert result.step_results[0].status == "failed"
    # Exactly 1 original attempt + _MAX_STEP_RETRIES automatic retries - no
    # more, no fewer - before giving up and surfacing the failure.
    assert executor.call_counts["only-step"] == _MAX_STEP_RETRIES + 1
