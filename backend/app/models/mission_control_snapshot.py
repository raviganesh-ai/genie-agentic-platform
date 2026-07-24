"""``MissionControlSnapshot``: the primary UI contract for the Mission Control Dashboard.

Assembled by ``app.services.mission_control_service.MissionControlService`` from
already-recorded Phase 1-6 state (workflow runs, handoffs, collaboration/decision
graph, approvals, governance events, shared memory) - this module defines the
shape only and performs no aggregation itself.

Field names use this codebase's snake_case convention; they correspond 1:1 to
the build spec's camelCase field names (``sessionId`` -> ``session_id``, etc.).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.approval_models import ApprovalRequest
from app.models.decision_graph import DecisionGraph
from app.models.handoff_models import AgentHandoff
from app.models.workflow_state import WorkflowStatus

TimelineEntryKind = Literal["workflow_step", "handoff", "approval", "collaboration"]

GovernanceStatus = Literal["compliant", "attention_required"]

__all__ = [
    "GovernanceStatus",
    "MemoryUpdateSummary",
    "MissionControlSnapshot",
    "TimelineEntry",
    "TimelineEntryKind",
]


class TimelineEntry(BaseModel):
    """A single chronological entry rendered on the Mission Control timeline."""

    model_config = ConfigDict(extra="forbid")

    kind: TimelineEntryKind
    label: str = Field(min_length=1)
    agent_id: str | None = None
    timestamp: datetime


class MemoryUpdateSummary(BaseModel):
    """A lightweight summary of one Shared Collaboration Memory write."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)
    classification: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    timestamp: datetime


class MissionControlSnapshot(BaseModel):
    """The full state the Mission Control Dashboard renders for one session.

    ``business_value_score``/``risk_score``/``readiness_score`` are derived
    heuristically from real, already-recorded workflow/approval/governance
    state (see ``MissionControlService``) - never hardcoded or synthetic,
    per "Do not use hardcoded demo data in production." A dedicated scoring
    engine is a reasonable later-phase enhancement.
    """

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1)
    workflow_run_id: str | None = None
    workflow_status: WorkflowStatus | None = None
    mission_progress: float = Field(ge=0.0, le=1.0)
    active_agents: list[str] = Field(default_factory=list)
    completed_agents: list[str] = Field(default_factory=list)
    blocked_agents: list[str] = Field(default_factory=list)
    current_workflow_step: str | None = None
    timeline: list[TimelineEntry] = Field(default_factory=list)
    approvals: list[ApprovalRequest] = Field(default_factory=list)
    handoffs: list[AgentHandoff] = Field(default_factory=list)
    memory_updates: list[MemoryUpdateSummary] = Field(default_factory=list)
    decision_graph: DecisionGraph | None = None
    governance_status: GovernanceStatus = "compliant"
    business_value_score: float = Field(ge=0.0, le=1.0)
    risk_score: float = Field(ge=0.0, le=1.0)
    readiness_score: float = Field(ge=0.0, le=1.0)
