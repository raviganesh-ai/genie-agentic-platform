"""Peer Review gate report domain model.

Genie's Governance Reviewer agent acts as a "Peer Reviewer" custodian: it
consolidates the Security Assessment Agent's and Test Generation Agent's
findings with its own architecture and code-quality review into a single
verdict across four hard gates (security, test coverage, architecture,
code quality). This module never invents or overrides that verdict - it
only parses the agent's own structured, marker-line output (see
``app.services.peer_review_service``), following the exact same
"agents return marker lines, never JSON" convention already established by
``app.models.requirements_qualification``.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "GateName",
    "GateStatus",
    "GovernanceFinding",
    "GovernanceGateReport",
    "GovernanceGateReportStatus",
    "PeerReviewDecision",
]

GateStatus = Literal["pass", "fail"]

GateName = Literal["security", "test_coverage", "architecture", "code_quality"]

PeerReviewDecision = Literal["approved", "blocked"]

GovernanceGateReportStatus = Literal["pending", "reviewed", "undetermined"]
"""
- pending: the governance-review step has not completed yet.
- reviewed: the step completed and its output contained a parseable gate
  verdict.
- undetermined: the step completed but its output did not contain a
  parseable verdict (e.g. a local/dev deterministic stub, or the agent
  did not follow the prompt's format).
"""


class GovernanceFinding(BaseModel):
    """A single open finding raised against one of the four Peer Review gates."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    gate: GateName
    severity: Literal["critical", "high", "medium", "low"]
    description: str = Field(min_length=1)
    recommendation: str = Field(default="")


class GovernanceGateReport(BaseModel):
    """The Governance Reviewer's own consolidated Peer Review verdict."""

    model_config = ConfigDict(extra="forbid")

    status: GovernanceGateReportStatus
    security_gate: GateStatus | None = None
    test_coverage_gate: GateStatus | None = None
    architecture_gate: GateStatus | None = None
    code_quality_gate: GateStatus | None = None
    findings: list[GovernanceFinding] = Field(default_factory=list)
    decision: PeerReviewDecision | None = Field(
        default=None,
        description="The agent's own stated PEER_REVIEW_DECISION, if available.",
    )
    assessed_by_agent_id: str | None = Field(
        default=None,
        description="The agent id that produced the assessed step output, if the step has run.",
    )
