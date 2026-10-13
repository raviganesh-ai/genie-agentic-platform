"""Requirement Discovery open-decisions model.

Captures requirements the Requirements Analyst agent flagged as ambiguous
or borderline during extraction - per the ``requirements-extraction-v1``
prompt template's existing rule ("if a requirement is ambiguous or seems
borderline, err on the side of MUST-HAVE rather than filtering it out"),
every such default must also be listed under the prompt's own
"Open Decisions Requiring Customer Confirmation:" heading so a human can
see explicitly which items were judgment calls rather than clearly stated
asks, instead of a silent default being folded invisibly into the
Must-Have list. Genie never invents or overrides the agent's own
reasoning here - it only reports the agent's own stated list (see
``app.services.requirements_service``).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["RequirementsOpenDecisions", "RequirementsOpenDecisionsStatus"]

RequirementsOpenDecisionsStatus = Literal[
    "pending",
    "none_found",
    "has_open_decisions",
    "undetermined",
]
"""
- pending: the requirements-analysis step has not completed yet.
- none_found: the step completed and its output's heading-ordered format
  omitted the "Open Decisions Requiring Customer Confirmation:" section
  entirely - the same "omit a heading when you have no items for it"
  convention the prompt already uses for every other section (Goals,
  Risks, Assumptions, ...), meaning the agent found nothing ambiguous.
- has_open_decisions: the step completed and the agent listed one or more
  items under that heading.
- undetermined: the step completed but produced no output text at all
  (e.g. a failed step, or a local/dev deterministic stub with no real
  content).
"""


class RequirementsOpenDecisions(BaseModel):
    """Requirements the agent flagged as needing explicit customer confirmation."""

    model_config = ConfigDict(extra="forbid")

    status: RequirementsOpenDecisionsStatus
    open_decisions: list[str] = Field(
        default_factory=list,
        description=(
            "Each item the agent listed under its own 'Open Decisions "
            "Requiring Customer Confirmation:' heading, verbatim."
        ),
    )
    assessed_by_agent_id: str | None = Field(
        default=None,
        description="The agent id that produced the assessed step output, if the step has run.",
    )
