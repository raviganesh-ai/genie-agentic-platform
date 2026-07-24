"""Foundry agent synchronization result domain models (Phase 10A)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SynchronizationStatus = Literal[
    "synchronized",
    "drift_detected",
    "validation_failed",
    "not_found",
    "provisioning_required",
]

ValidationStatus = Literal["passed", "failed", "not_validated"]

__all__ = [
    "SynchronizationReport",
    "SynchronizationResult",
    "SynchronizationStatus",
    "ValidationStatus",
]


class SynchronizationResult(BaseModel):
    """Outcome of synchronizing a single configured agent against Azure AI Foundry."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1)
    status: SynchronizationStatus
    issues: list[str] = Field(default_factory=list)
    synchronized_at: datetime


class SynchronizationReport(BaseModel):
    """Aggregate result of one full synchronization run across every agent."""

    model_config = ConfigDict(extra="forbid")

    results: list[SynchronizationResult] = Field(default_factory=list)
    generated_at: datetime

    @property
    def has_blocking_failures(self) -> bool:
        """True if any agent failed validation or could not be found in Foundry."""

        return any(result.status in ("validation_failed", "not_found") for result in self.results)

    def for_agent(self, agent_id: str) -> SynchronizationResult | None:
        for result in self.results:
            if result.agent_id == agent_id:
                return result
        return None
