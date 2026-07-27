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
        run = self._orchestrator.get_workflow_run(workflow_run_id)
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
