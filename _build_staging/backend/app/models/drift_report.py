"""Foundry agent drift detection domain models (Phase 10A)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DriftIssueType = Literal[
    "version_mismatch",
    "deployment_mismatch",
    "governance_policy_mismatch",
    "prompt_mismatch",
    "lifecycle_mismatch",
    "ownership_mismatch",
    "memory_configuration_mismatch",
]

DriftSeverity = Literal["low", "medium", "high", "critical"]

__all__ = ["DriftIssue", "DriftIssueType", "DriftReport", "DriftSeverity"]


class DriftIssue(BaseModel):
    """A single detected difference between an agent's current and expected configuration."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1)
    issue_type: DriftIssueType
    expected_value: str
    actual_value: str
    severity: DriftSeverity
    recommendation: str = Field(min_length=1)


class DriftReport(BaseModel):
    """Aggregate drift findings for a single agent as of one detection run."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1)
    issues: list[DriftIssue] = Field(default_factory=list)
    detected_at: datetime

    @property
    def has_critical_issues(self) -> bool:
        return any(issue.severity == "critical" for issue in self.issues)
