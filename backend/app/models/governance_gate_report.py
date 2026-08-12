"""Governance assessment domain model.

The Security Assessment Agent and Test Generation Agent each report their
own pass/fail gate verdict (security, test coverage) with findings and
remediation recommendations, read directly from that agent's own
structured, marker-line output (see ``app.services.peer_review_service`),
following the exact same "agents return marker lines, never JSON"
convention already established by ``app.models.requirements_qualification``.
This module never invents or overrides those verdicts - it only parses
what the agent itself stated.
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
]

GateStatus = Literal["pass", "fail"]

GateName = Literal["requirements", "security", "test_coverage", "architecture", "code_quality"]


class GovernanceFinding(BaseModel):
    """A single open finding raised against one of the governance gates."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    gate: GateName
    severity: Literal["critical", "high", "medium", "low"]
    description: str = Field(min_length=1)
    recommendation: str = Field(default="")


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
    that step completes. Never invents a verdict: mirrors the exact same
    "agents return marker-line text, Genie only reports what the agent
    itself stated" convention used throughout the platform.
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
    """Combined view of the two specialist agents' own gate assessments."""

    model_config = ConfigDict(extra="forbid")

    security_assessment: AgentAssessment
    test_generation: AgentAssessment

