"""Unit tests for DebuggingWorkflowService's lessons-learned parsing and
persistence (Genie platform-change Phase F).

_parse_lessons_learned is tested with crafted literal text (same
convention as app.services.requirements_service's _parse_verdict /
_parse_open_decisions - a pure string-parsing function, no LLM needed).
handle_failure's end-to-end persistence is tested against a real
InMemoryLessonsLearnedRepository with a minimal fake WorkflowExecutionService
double returning realistic crafted step output - the repository itself is
never mocked, only the (expensive, requires a real hermetic orchestrator)
WorkflowExecutionService is swapped for a lightweight stand-in.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.config.settings import Settings
from app.models.workflow_models import WorkflowRunResult, WorkflowStepResult
from app.orchestration.debugging_workflow_service import (
    DebuggingWorkflowService,
    _parse_lessons_learned,
)
from app.repositories.lessons_learned_repository import InMemoryLessonsLearnedRepository


def _settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[call-arg]


def test_parse_lessons_learned_returns_empty_when_heading_is_absent():
    text = "Root cause: a transient network blip.\nRemediation: retry the call."

    assert _parse_lessons_learned(text) == []


def test_parse_lessons_learned_returns_empty_for_the_explicit_no_change_sentinel():
    text = (
        "Root cause: an Azure Foundry outage, not a code or prompt defect.\n\n"
        "Lessons Learned (Prompt/Config Changes):\n"
        "No prompt or config change indicated.\n"
    )

    assert _parse_lessons_learned(text) == []


def test_parse_lessons_learned_extracts_suggested_prompt_and_change():
    text = (
        "Root cause: the generated Orchestrator never forwarded "
        "requested_product_domains to its MCP tool wrapper.\n\n"
        "Lessons Learned (Prompt/Config Changes):\n"
        "- [build-generation-component-v1] Require every field read from the "
        "parsed request to be forwarded to its corresponding tool call.\n"
        "- [test-generation-v1] Require a test asserting every submitted field "
        "reaches the backend unchanged.\n"
    )

    result = _parse_lessons_learned(text)

    assert result == [
        (
            "build-generation-component-v1",
            "Require every field read from the parsed request to be forwarded "
            "to its corresponding tool call.",
        ),
        (
            "test-generation-v1",
            "Require a test asserting every submitted field reaches the "
            "backend unchanged.",
        ),
    ]


def test_parse_lessons_learned_allows_no_prompt_id_when_not_confident():
    text = (
        "Lessons Learned (Prompt/Config Changes):\n"
        "- [] Possibly a prompt gap, but not confident which prompt is responsible.\n"
    )

    result = _parse_lessons_learned(text)

    assert result == [
        (None, "Possibly a prompt gap, but not confident which prompt is responsible.")
    ]


class _FakeExecutionService:
    """Minimal stand-in for WorkflowExecutionService - returns a crafted
    WorkflowRunResult instead of actually running a hermetic workflow, since
    this test only needs to prove handle_failure correctly extracts and
    persists lessons learned from whatever output_text the diagnose-failure
    step produced."""

    def __init__(self, step_output_text: str) -> None:
        self._step_output_text = step_output_text

    async def start_workflow(self, *, workflow_id, session_id, trace_id, step_inputs=None, **_):
        return WorkflowRunResult(
            workflow_run_id="run-1",
            workflow_id=workflow_id,
            session_id=session_id,
            status="completed",
            step_results=[
                WorkflowStepResult(
                    step_id="diagnose-failure",
                    agent_id="debugging-agent",
                    status="completed",
                    output_text=self._step_output_text,
                    started_at=datetime.now(UTC),
                    completed_at=datetime.now(UTC),
                )
            ],
        )


async def test_handle_failure_persists_a_lesson_learned_record_for_a_real_repository():
    execution_service = _FakeExecutionService(
        "Root cause analysis here.\n\n"
        "Lessons Learned (Prompt/Config Changes):\n"
        "- [build-generation-v1] Add an explicit field-forwarding requirement.\n"
    )
    repository = InMemoryLessonsLearnedRepository()
    service = DebuggingWorkflowService(
        settings=_settings(debugging_workflow_id="debugging-workflow"),
        execution_service=execution_service,  # type: ignore[arg-type]
        lessons_learned_repository=repository,
    )

    await service.handle_failure(
        session_id="session-1", source_agent_id="agent-a", error="boom"
    )
    open_lessons = await service.list_open_lessons_learned()

    assert len(open_lessons) == 1
    assert open_lessons[0].suggested_prompt_id == "build-generation-v1"
    assert open_lessons[0].suggested_change == "Add an explicit field-forwarding requirement."
    assert open_lessons[0].status == "open"
    assert open_lessons[0].session_id == "session-1"
    assert open_lessons[0].workflow_run_id == "run-1"


async def test_handle_failure_persists_nothing_for_the_explicit_no_change_sentinel():
    execution_service = _FakeExecutionService(
        "Root cause: a transient Azure outage.\n\n"
        "Lessons Learned (Prompt/Config Changes):\n"
        "No prompt or config change indicated.\n"
    )
    repository = InMemoryLessonsLearnedRepository()
    service = DebuggingWorkflowService(
        settings=_settings(debugging_workflow_id="debugging-workflow"),
        execution_service=execution_service,  # type: ignore[arg-type]
        lessons_learned_repository=repository,
    )

    await service.handle_failure(
        session_id="session-2", source_agent_id="agent-a", error="boom"
    )

    assert await service.list_open_lessons_learned() == []
