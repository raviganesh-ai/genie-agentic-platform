"""Governance event domain model.

A single, canonical ``GovernanceEvent`` shape is used for every category of
governed activity tracked by Phase 5 (see "Agent Execution Governance" and
"Governance Requirements" in ``.github/copilot-instructions.md``): agent
registration, agent versions, agent lifecycle, agent executions, agent
communication, memory reads/writes, tool requests, policy evaluations,
denied access events, and (added post-Phase-8, for Responsible AI
Accountability) human checkpoint confirmations - a person explicitly
proceeding the Discovery Wizard past a workflow stage. A single shape
(rather than one class per category) keeps storage, querying, and replay
reconstruction uniform.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

GovernanceEventCategory = Literal[
    "agent_registration",
    "agent_version",
    "agent_lifecycle",
    "agent_execution",
    "agent_communication",
    "memory_read",
    "memory_write",
    "tool_request",
    "policy_evaluation",
    "access_denied",
    "human_checkpoint_confirmation",
]

__all__ = ["GovernanceEvent", "GovernanceEventCategory"]


class GovernanceEvent(BaseModel):
    """A single governance-traceable event, scoped to a session and trace.

    ``trace_id`` links this event to the decision/recommendation/approval it
    relates to, enabling full decision lineage and session replay
    reconstruction. ``detail`` is an opaque, externally supplied payload
    (e.g. tool name, policy id, denial reason) - never customer content by
    itself, but callers must not place customer/business secrets in it.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    category: GovernanceEventCategory
    session_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    agent_id: str | None = None
    timestamp: datetime
    detail: dict[str, Any] = Field(default_factory=dict)
