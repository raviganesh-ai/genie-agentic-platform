"""Debugging workflow service.

Implements "DEBUGGING WORKFLOW" for Phase 6: on FailureDetected, routes the
failure to the configured debugging workflow (``Settings.debugging_workflow_id``)
and executes it through the exact same ``WorkflowRuntime`` /
``AzureAgentGateway`` path as every other workflow - automatically
inheriting fail-closed and governance guarantees with no bespoke execution
path. Debugging agents are Azure-hosted; this service never executes
anything locally in production.

Genie platform-change Phase F: also closes the "diagnosed failure ->
prompt registry change candidate" feedback loop. The Debugging Agent's own
``failure-diagnosis-v1`` output states, in a structured, parseable format,
whether its diagnosed root cause traces to a gap in an existing prompt's
instructions (see the prompt's own "Lessons Learned (Prompt/Config
Changes):" heading) - this service extracts that structured statement and
persists it as a queryable ``LessonLearned`` record for a human to review
and apply through the normal git/PR process, rather than relying on
someone remembering to make that connection by hand (as happened once, ad
hoc, in this platform's own development history). It never invents or
applies a prompt change itself.
"""
from __future__ import annotations

import re
from typing import Final
from uuid import uuid4

from app.config.settings import Settings
from app.models.lessons_learned import LessonLearned
from app.models.workflow_models import WorkflowRunResult, WorkflowStepInput
from app.orchestration.workflow_execution_service import WorkflowExecutionService
from app.repositories.lessons_learned_repository import LessonsLearnedRepository

__all__ = ["DebuggingWorkflowService"]

_DIAGNOSE_FAILURE_STEP_ID: Final = "diagnose-failure"
_LESSONS_LEARNED_HEADING_PATTERN: Final = re.compile(
    r"^[ \t]*Lessons Learned \(Prompt/Config Changes\):[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
_LESSON_ITEM_PATTERN: Final = re.compile(r"^-\s*\[([^\]]*)\]\s*(.+)$")
_NO_LESSON_SENTINEL: Final = "no prompt or config change indicated."


def _parse_lessons_learned(output_text: str) -> list[tuple[str | None, str]]:
    """Extracts the Debugging Agent's own stated prompt/config change
    candidates from its "Lessons Learned (Prompt/Config Changes):" heading
    (see ``failure-diagnosis-v1``'s own required format).

    Returns a list of ``(suggested_prompt_id, suggested_change)`` pairs -
    empty when the heading is absent (the agent did not follow the
    required format - a diagnosis omitting this heading entirely never
    implies "nothing to report", so callers must not persist a lesson for
    it) or when the agent stated its explicit "No prompt or config change
    indicated." sentinel line (a real, considered "no" answer, not a
    guess).
    """

    heading_match = _LESSONS_LEARNED_HEADING_PATTERN.search(output_text)
    if heading_match is None:
        return []

    lessons: list[tuple[str | None, str]] = []
    for line in output_text[heading_match.end() :].splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower() == _NO_LESSON_SENTINEL:
            return []
        item_match = _LESSON_ITEM_PATTERN.match(stripped)
        if item_match is None:
            break
        prompt_id = item_match.group(1).strip()
        lessons.append((prompt_id or None, item_match.group(2).strip()))
    return lessons


class DebuggingWorkflowService:
    """Routes detected failures into the configured debugging workflow."""

    def __init__(
        self,
        *,
        settings: Settings,
        execution_service: WorkflowExecutionService,
        lessons_learned_repository: LessonsLearnedRepository,
    ) -> None:
        self._settings = settings
        self._execution_service = execution_service
        self._lessons_learned_repository = lessons_learned_repository

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
            _DIAGNOSE_FAILURE_STEP_ID: WorkflowStepInput(
                step_id=_DIAGNOSE_FAILURE_STEP_ID,
                variables={
                    "failure_details": (
                        f"source_agent_id={source_agent_id}; session_id={session_id}; "
                        f"error={error}"
                    )
                },
            )
        }
        result = await self._execution_service.start_workflow(
            workflow_id=self._settings.debugging_workflow_id,
            session_id=session_id,
            trace_id=resolved_trace_id,
            step_inputs=step_inputs,
        )
        await self._record_lessons_learned(result)
        return result

    async def _record_lessons_learned(self, result: WorkflowRunResult) -> None:
        step = next(
            (r for r in result.step_results if r.step_id == _DIAGNOSE_FAILURE_STEP_ID), None
        )
        if step is None or step.status != "completed" or not step.output_text:
            return
        for suggested_prompt_id, suggested_change in _parse_lessons_learned(step.output_text):
            await self._lessons_learned_repository.put(
                LessonLearned(
                    id=str(uuid4()),
                    session_id=result.session_id,
                    workflow_run_id=result.workflow_run_id,
                    diagnosed_at=step.completed_at,
                    suggested_prompt_id=suggested_prompt_id,
                    suggested_change=suggested_change,
                )
            )

    async def list_open_lessons_learned(self) -> list[LessonLearned]:
        """Lists every diagnosed failure's prompt/config change candidate
        still awaiting human review - the queryable backlog Phase F exists
        to create, in place of relying on someone remembering to connect a
        diagnosis back to a prompt change by hand."""

        return await self._lessons_learned_repository.list_open()
