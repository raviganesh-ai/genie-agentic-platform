"""Approval framework domain models.

Supports the "APPROVAL FRAMEWORK" and "SESSION REPLAY" requirements for
Phase 5: an ``ApprovalCheckpoint`` is a configured gate (e.g. "approve
proposed architecture"); an ``ApprovalRequest`` tracks one instance of that
gate being reached for a specific subject; an ``ApprovalDecision`` records
a human reviewer's outcome; an ``ApprovalAuditRecord`` captures every state
transition for full auditability and session replay.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ApprovalRequestStatus = Literal["pending", "approved", "rejected", "expired"]
ApprovalDecisionOutcome = Literal["approved", "rejected"]
ApprovalAuditEvent = Literal["requested", "approved", "rejected", "expired"]

__all__ = [
    "ApprovalAuditEvent",
    "ApprovalAuditRecord",
    "ApprovalCheckpoint",
    "ApprovalDecision",
    "ApprovalDecisionOutcome",
    "ApprovalRequest",
    "ApprovalRequestStatus",
]


class ApprovalCheckpoint(BaseModel):
    """A single, externally configured human approval gate.

    Loaded from ``config/policies/approval_policy.yaml`` - approval
    checkpoints are never hardcoded in source, per the Configuration Rules
    in ``.github/copilot-instructions.md``.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    required: bool = True


class ApprovalRequest(BaseModel):
    """A single instance of an ``ApprovalCheckpoint`` being reached."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    checkpoint_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    requested_by_agent_id: str = Field(min_length=1)
    subject_type: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    status: ApprovalRequestStatus = "pending"
    requested_at: datetime
    expires_at: datetime | None = None


class ApprovalDecision(BaseModel):
    """A human reviewer's outcome for one ``ApprovalRequest``."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    decision: ApprovalDecisionOutcome
    decided_by: str = Field(min_length=1)
    decided_at: datetime
    rationale: str = ""


class ApprovalAuditRecord(BaseModel):
    """A single state-transition audit entry for one ``ApprovalRequest``."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    event: ApprovalAuditEvent
    timestamp: datetime
    actor: str = Field(min_length=1)
    detail: str = ""
