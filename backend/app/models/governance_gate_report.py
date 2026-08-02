"""Peer Review gate report domain model.

Genie's Peer Review Agent acts as an unbiased project-leader-style reviewer:
it independently confirms the generated build actually satisfies the
approved requirements, and consolidates the Security Assessment Agent's and
Test Generation Agent's findings with its own architecture and
code-quality review into a single verdict across five hard gates
(requirements, security, test coverage, architecture, code quality). This module never
invents or overrides that verdict - it only parses the agent's own
structured, marker-line output (see
``app.services.peer_review_service``), following the exact same
"agents return marker lines, never JSON" convention already established by
``app.models.requirements_qualification``.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AgentAssessment",
    "AgentAssessmentStatus",
    "AgentAssessmentsReport",
    "GateName",
    "GateStatus",
    "GovernanceFinding",
    "GovernanceGateReport",
    "GovernanceGateReportStatus",
    "PeerReviewDecision",
]

GateStatus = Literal["pass", "fail"]

GateName = Literal["requirements", "security", "test_coverage", "architecture", "code_quality"]

PeerReviewDecision = Literal["approved", "blocked"]

GovernanceGateReportStatus = Literal["pending", "reviewed", "undetermined"]
"""
- pending: the peer-review step has not completed yet.
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
    """The Peer Review Agent's own consolidated Peer Review verdict."""

    model_config = ConfigDict(extra="forbid")

    status: GovernanceGateReportStatus
    requirements_gate: GateStatus | None = None
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


AgentAssessmentStatus = Literal["pending", "reviewed", "undetermined"]
"""
- pending: the underlying step (security-assessment or test-generation) has
  not completed yet.
- reviewed: the step completed and its own output contained a parseable
  single-gate verdict.
- undetermined: the step completed but its output did not contain a
  parseable verdict.
"""


class AgentAssessment(BaseModel):
    """One specialist agent's own single-gate assessment (Security Assessment
    Agent's security gate, or Test Generation Agent's test-coverage gate),
    read directly from that agent's own step output - available as soon as
    that step completes, without waiting for the Peer Review Agent's
    slower consolidated Peer Review verdict (see ``GovernanceGateReport``)
    to also finish. Never invents a verdict: mirrors the exact same
    "agents return marker-line text, Genie only reports what the agent
    itself stated" convention as ``GovernanceGateReport``.
    """
    model_config = ConfigDict(extra="forbid")

    status: AgentAssessmentStatus
    gate: GateStatus | None = None
    summary: str = Field(
        default="", description="The agent's full raw narrative output text, verbatim."
    )
    findings: list[GovernanceFinding] = Field(default_factory=list)
    tests_generated: int = Field(
        default=0,
        description="Count of fenced code blocks in the Test Generation Agent's own "
        "output (a real, directly-counted quantity - never an estimated or invented "
        "percentage). Always 0 for the security assessment.",
    )
    assessed_by_agent_id: str | None = None


class AgentAssessmentsReport(BaseModel):
    """Combined early-visibility view of the two specialist agents that feed the
    Peer Review Agent's consolidated Peer Review verdict."""

    model_config = ConfigDict(extra="forbid")

    security_assessment: AgentAssessment
    test_generation: AgentAssessment
