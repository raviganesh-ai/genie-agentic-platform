"""Unit tests for ``WorkshopService.regenerate_build_artifacts``.

Guards against a real bug this method was written to avoid: every step in
solution-discovery-workflow executes as ``genie-orchestrator`` (see
config/workflows/registry.yaml), so resolving "which step to resume" by
agent id (as ``chat_with_agent`` does via ``_step_id_for_agent``) always
resolves to the *first* such step rather than ``build-solution``.
``regenerate_build_artifacts`` must target the ``build-solution`` step id
directly instead.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.workflow_models import WorkflowRunResult, WorkflowStepInput, WorkflowStepResult
from app.services.session_service import create_session_service
from app.services.workshop_service import WorkshopService


class _FakeOrchestrator:
    """Duck-typed stand-in recording the ``resume_workflow`` call it received."""

    def __init__(self, *, run: WorkflowRunResult) -> None:
        self._run = run
        self.resume_calls: list[dict] = []

    def get_workflow_run(self, workflow_run_id: str) -> WorkflowRunResult | None:
        return self._run if workflow_run_id == self._run.workflow_run_id else None

    async def resume_workflow(
        self,
        *,
        workflow_run_id: str,
        session_id: str,
        trace_id: str | None,
        step_inputs: dict[str, WorkflowStepInput],
    ) -> WorkflowRunResult:
        self.resume_calls.append(
            {
                "workflow_run_id": workflow_run_id,
                "session_id": session_id,
                "trace_id": trace_id,
                "step_inputs": step_inputs,
            }
        )
        return self._run


def _step_result(step_id: str, *, output_text: str) -> WorkflowStepResult:
    now = datetime.now(UTC)
    return WorkflowStepResult(
        step_id=step_id,
        agent_id="genie-orchestrator",
        status="completed",
        output_text=output_text,
        started_at=now,
        completed_at=now,
    )


@pytest.fixture
def run() -> WorkflowRunResult:
    return WorkflowRunResult(
        workflow_run_id="run-1",
        workflow_id="solution-discovery-workflow",
        session_id="session-1",
        status="completed",
        waves=[["analyze-requirements"], ["design-architecture"], ["build-solution"]],
        step_results=[
            _step_result("analyze-requirements", output_text="Extracted requirements."),
            _step_result("design-architecture", output_text="Recommended architecture."),
            _step_result("build-solution", output_text="```tsx\n// agent: ui\nconst x = 1;\n```"),
        ],
    )


async def test_regenerate_build_artifacts_targets_build_solution_step_directly(
    run: WorkflowRunResult,
) -> None:
    orchestrator = _FakeOrchestrator(run=run)
    session_service = create_session_service(orchestrator=orchestrator)  # type: ignore[arg-type]
    session = await session_service.create_session(owner_user_id="user-1", title="t")
    service = WorkshopService(orchestrator=orchestrator, session_service=session_service)  # type: ignore[arg-type]

    result = await service.regenerate_build_artifacts(
        session_id=session.id,
        requesting_user_id="user-1",
        workflow_run_id="run-1",
        instruction="Add retry logic to the orchestrator.",
        trace_id="trace-1",
    )

    assert result is run
    assert len(orchestrator.resume_calls) == 1
    call = orchestrator.resume_calls[0]
    assert call["workflow_run_id"] == "run-1"
    assert call["trace_id"] == "trace-1"
    assert list(call["step_inputs"].keys()) == ["build-solution"]
    step_input = call["step_inputs"]["build-solution"]
    assert step_input.step_id == "build-solution"
    assert step_input.variables == {"user_message": "Add retry logic to the orchestrator."}
