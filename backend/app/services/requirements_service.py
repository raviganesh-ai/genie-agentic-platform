"""Requirement Discovery application service.

Reads whether the requirements captured for a workflow run (via the
Requirements Analyst agent's requirements-analysis step) genuinely qualify
for a multi-agent agentic AI workflow, or whether a simpler, non-agentic
solution would do - and, if not, why. The agent itself makes this
judgment call (per the updated ``requirements-extraction-v1`` prompt
template) and states it as a structured, parseable verdict at the end of
its output; this service only extracts that verdict from the already-
recorded step output, it never invents or overrides the agent's own
reasoning.
"""
from __future__ import annotations

import re
from typing import Final

from app.models.requirements_open_decisions import RequirementsOpenDecisions
from app.models.requirements_qualification import RequirementsQualification
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.services.session_service import SessionService
from app.services.workshop_service import UnknownWorkflowRunError

__all__ = ["RequirementsService", "create_requirements_service"]

_VERDICT_PATTERN: Final = re.compile(
    r"AGENTIC_WORKFLOW_QUALIFICATION:\s*(QUALIFIED|NOT_QUALIFIED)", re.IGNORECASE
)
_REASON_PATTERN: Final = re.compile(
    r"QUALIFICATION_REASON:\s*(.+)", re.IGNORECASE | re.DOTALL
)
_OPEN_DECISIONS_HEADING_PATTERN: Final = re.compile(
    r"^[ \t]*Open Decisions Requiring Customer Confirmation:[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
# Every other heading requirements-extraction-v1's OUTPUT FORMAT can emit -
# used only to know where the Open Decisions section's own bullet list
# ends, in case the agent places it immediately before another heading
# with no blank line in between.
_NEXT_REQUIREMENTS_HEADING_PATTERN: Final = re.compile(
    r"^[ \t]*(?:Goals|Must-Have Functional Requirements|Nice-to-Have Functional "
    r"Requirements|Non-Functional Requirements|Risks|Assumptions|Constraints|"
    r"Critical path \(must build first\)|Acceptance Criteria):[ \t]*$",
    re.IGNORECASE,
)


def _parse_verdict(output_text: str) -> tuple[bool, str | None] | None:
    """Extracts the agent's stated qualification verdict from its output text.

    Returns ``(qualifies, reason)`` if a structured verdict block is
    present, or ``None`` if it is not (e.g. the agent did not follow the
    prompt's format, or this is a deterministic local-dev stub with no real
    reasoning).
    """

    verdict_match = _VERDICT_PATTERN.search(output_text)
    if verdict_match is None:
        return None
    qualifies = verdict_match.group(1).upper() == "QUALIFIED"
    reason_match = _REASON_PATTERN.search(output_text)
    reason = reason_match.group(1).strip() if reason_match else None
    return qualifies, reason


def _parse_open_decisions(output_text: str) -> list[str]:
    """Extracts the items the agent listed under its own "Open Decisions
    Requiring Customer Confirmation:" heading (see
    ``requirements-extraction-v1``'s OUTPUT FORMAT contract) - every
    requirement the agent defaulted to MUST-HAVE because it was ambiguous
    or borderline must also appear here, rather than being silently folded
    into the Must-Have list with no trace.

    Returns an empty list when the heading is absent - the same "omit the
    heading entirely when you have no items for it" convention this exact
    prompt already uses for every other heading (Goals, Risks, Assumptions,
    ...), meaning the agent found nothing ambiguous, never a guess that
    something was missed.
    """

    heading_match = _OPEN_DECISIONS_HEADING_PATTERN.search(output_text)
    if heading_match is None:
        return []

    items: list[str] = []
    for line in output_text[heading_match.end() :].splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _NEXT_REQUIREMENTS_HEADING_PATTERN.match(stripped):
            break
        items.append(stripped.lstrip("-*•").strip())
    return items


class RequirementsService:
    """Reads the agentic-workflow qualification verdict for a workflow run."""

    def __init__(
        self,
        *,
        orchestrator: AgentOrchestrator,
        session_service: SessionService,
        qualification_step_id: str,
    ) -> None:
        self._orchestrator = orchestrator
        self._session_service = session_service
        self._qualification_step_id = qualification_step_id

    async def get_qualification(
        self, *, session_id: str, requesting_user_id: str, workflow_run_id: str
    ) -> RequirementsQualification:
        await self._session_service.get_session(
            session_id=session_id, requesting_user_id=requesting_user_id
        )
        run = await self._orchestrator.get_workflow_run(workflow_run_id)
        if run is None:
            raise UnknownWorkflowRunError(f"Unknown workflow run id '{workflow_run_id}'.")

        step = next(
            (r for r in run.step_results if r.step_id == self._qualification_step_id), None
        )
        if step is None or step.status != "completed":
            return RequirementsQualification(status="pending")

        parsed = _parse_verdict(step.output_text or "")
        if parsed is None:
            return RequirementsQualification(status="undetermined", assessed_by_agent_id=step.agent_id)

        qualifies, reason = parsed
        return RequirementsQualification(
            status="qualified" if qualifies else "not_qualified",
            reason=reason,
            assessed_by_agent_id=step.agent_id,
        )

    async def get_open_decisions(
        self, *, session_id: str, requesting_user_id: str, workflow_run_id: str
    ) -> RequirementsOpenDecisions:
        await self._session_service.get_session(
            session_id=session_id, requesting_user_id=requesting_user_id
        )
        run = await self._orchestrator.get_workflow_run(workflow_run_id)
        if run is None:
            raise UnknownWorkflowRunError(f"Unknown workflow run id '{workflow_run_id}'.")

        step = next(
            (r for r in run.step_results if r.step_id == self._qualification_step_id), None
        )
        if step is None or step.status != "completed":
            return RequirementsOpenDecisions(status="pending")

        if not step.output_text:
            return RequirementsOpenDecisions(
                status="undetermined", assessed_by_agent_id=step.agent_id
            )

        open_decisions = _parse_open_decisions(step.output_text)
        return RequirementsOpenDecisions(
            status="has_open_decisions" if open_decisions else "none_found",
            open_decisions=open_decisions,
            assessed_by_agent_id=step.agent_id,
        )


def create_requirements_service(
    *,
    orchestrator: AgentOrchestrator,
    session_service: SessionService,
    qualification_step_id: str,
) -> RequirementsService:
    return RequirementsService(
        orchestrator=orchestrator,
        session_service=session_service,
        qualification_step_id=qualification_step_id,
    )
