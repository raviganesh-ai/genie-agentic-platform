"""Strongly typed memory tier domain models.

Implements the three-tier Memory Architecture described in
``.github/copilot-instructions.md``: Personal Agent Memory, Shared
Collaboration Memory, and Enterprise Knowledge Memory. Every record carries
a ``MemoryLineage`` capturing full decision lineage/provenance so every
memory write is governance-traceable.

No business/customer content is modeled here: ``content`` is an opaque,
externally supplied ``dict[str, Any]`` for every tier, since actual
requirements/findings/architectures are produced at runtime by
Foundry-hosted agents, never hardcoded in source.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agents.models import MemoryTier

__all__ = [
    "ApprovalStatus",
    "EnterpriseKnowledgeRecord",
    "EnterpriseMemoryClassification",
    "MemoryAccessDeniedError",
    "MemoryLineage",
    "MemoryTier",
    "PersonalMemoryClassification",
    "PersonalMemoryRecord",
    "SharedMemoryClassification",
    "SharedMemoryRecord",
]


class MemoryAccessDeniedError(RuntimeError):
    """Raised when a memory operation is denied by ``MemoryAccessPolicyService``."""

ApprovalStatus = Literal["not_required", "pending", "approved", "rejected"]

PersonalMemoryClassification = Literal[
    "observation",
    "working_note",
    "finding",
    "reasoning_summary",
    "work_product",
]

SharedMemoryClassification = Literal[
    "goal",
    "requirement",
    "constraint",
    "risk",
    "assumption",
    "approval",
    "architecture_finding",
    "roadmap_artifact",
]

EnterpriseMemoryClassification = Literal[
    "industry_pattern",
    "reference_architecture",
    "reusable_knowledge",
]


class MemoryLineage(BaseModel):
    """Decision lineage/provenance recorded on every memory write.

    Captures ``sessionId``, ``traceId``, ``agentId``, ``agentVersion``,
    ``timestamp``, ``evidenceReferences``, ``confidenceScore``, and
    ``approvalStatus`` per the Memory Architecture / Governance
    Requirements in ``.github/copilot-instructions.md``. ``classification``
    is recorded on the owning record itself (its type differs per tier).
    """

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    agent_version: str = Field(min_length=1)
    timestamp: datetime
    evidence_references: list[str] = Field(default_factory=list)
    confidence_score: float = Field(ge=0.0, le=1.0)
    approval_status: ApprovalStatus = "not_required"


class PersonalMemoryRecord(BaseModel):
    """A single Personal Agent Memory entry: isolated per session and agent."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    classification: PersonalMemoryClassification
    content: dict[str, Any]
    lineage: MemoryLineage

    @model_validator(mode="after")
    def _lineage_matches_record(self) -> PersonalMemoryRecord:
        if self.lineage.session_id != self.session_id:
            raise ValueError("lineage.session_id must match record.session_id.")
        if self.lineage.agent_id != self.agent_id:
            raise ValueError("lineage.agent_id must match record.agent_id.")
        return self


class SharedMemoryRecord(BaseModel):
    """A single Shared Collaboration Memory entry, keyed by ``id`` within a session.

    ``version`` increments on every approved overwrite (see
    ``SharedMemoryStore``), giving a simple, auditable overwrite history
    without requiring a full event-sourced store in Phase 4.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    classification: SharedMemoryClassification
    content: dict[str, Any]
    lineage: MemoryLineage
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _lineage_matches_record(self) -> SharedMemoryRecord:
        if self.lineage.session_id != self.session_id:
            raise ValueError("lineage.session_id must match record.session_id.")
        return self


class EnterpriseKnowledgeRecord(BaseModel):
    """A single Enterprise Knowledge Memory entry.

    Cross-session by design (industry patterns and reference architectures
    are reusable across customer engagements); ``lineage.session_id``
    records the session that originally contributed the knowledge.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    classification: EnterpriseMemoryClassification
    content: dict[str, Any]
    lineage: MemoryLineage
