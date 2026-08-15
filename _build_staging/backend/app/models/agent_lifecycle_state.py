"""Foundry agent lifecycle state domain model (Phase 10A).

Lifecycle state is *runtime/operational* state (tracked by
``FoundryAgentLifecycleService``) and deliberately kept off
``AgentDefinition`` (static, externally configured identity) - see
"Do not redesign: AgentRegistryService" in the Phase 10A instructions.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AgentLifecycleState = Literal[
    "draft",
    "registered",
    "provisioned",
    "validated",
    "deprecated",
    "retired",
]

__all__ = ["AgentLifecycleState", "AgentLifecycleTransition"]


class AgentLifecycleTransition(BaseModel):
    """A single recorded lifecycle state transition for one agent.

    Every transition is also emitted as a governance lifecycle event (see
    ``GovernanceService.record_lifecycle_event``), so this record exists
    purely for fast, structured in-process history/inventory queries.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    from_state: AgentLifecycleState | None = None
    to_state: AgentLifecycleState
    reason: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    occurred_at: datetime
