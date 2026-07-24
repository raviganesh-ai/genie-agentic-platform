"""Recommendation lineage domain model.

Captures exactly what a customer must be able to determine about any
recommendation Genie produces (see "LINEAGE" requirements for Phase 5):
which agent created it, which evidence was used, which memory entries
contributed, and which approvals were granted.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["RecommendationLineage"]


class RecommendationLineage(BaseModel):
    """Full provenance for a single recommendation produced by an agent."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    recommendation_id: str = Field(min_length=1)
    recommendation_type: str = Field(min_length=1)
    produced_by_agent_id: str = Field(min_length=1)
    produced_by_agent_version: str = Field(min_length=1)
    evidence_references: list[str] = Field(default_factory=list)
    memory_references: list[str] = Field(default_factory=list)
    approval_ids: list[str] = Field(default_factory=list)
    confidence_score: float = Field(ge=0.0, le=1.0)
    timestamp: datetime
