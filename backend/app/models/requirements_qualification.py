"""Requirement Discovery agentic-workflow qualification model.

Captures whether the requirements captured during discovery (via the
Requirements Analyst agent's requirements-extraction step) genuinely
warrant a multi-agent agentic AI workflow, as opposed to a simpler,
non-agentic solution. The agent itself makes this judgment call (per the
``requirements-extraction-v1`` prompt template) - Genie never invents or
overrides that reasoning, it only reports the agent's own stated verdict
(see ``app.services.requirements_service``).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["RequirementsQualification", "RequirementsQualificationStatus"]

RequirementsQualificationStatus = Literal[
    "pending",
    "qualified",
    "not_qualified",
    "undetermined",
]
"""
- pending: the requirements-analysis step has not completed yet.
- qualified: the agent's stated verdict is that an agentic workflow is warranted.
- not_qualified: the agent's stated verdict is that a simpler, non-agentic
  solution is more appropriate.
- undetermined: the step completed but its output did not contain a
  parseable verdict (e.g. a local/dev deterministic stub with no real
  reasoning, or the agent did not follow the prompt's format).
"""


class RequirementsQualification(BaseModel):
    """Whether captured requirements qualify for an agentic AI workflow."""

    model_config = ConfigDict(extra="forbid")

    status: RequirementsQualificationStatus
    reason: str | None = Field(
        default=None,
        description="The agent's own plain-language explanation of its verdict, if available.",
    )
    assessed_by_agent_id: str | None = Field(
        default=None,
        description="The agent id that produced the assessed step output, if the step has run.",
    )
