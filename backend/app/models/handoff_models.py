"""Agent handoff domain model.

Implements the "AGENT HANDOFF MODEL" for Phase 6: a record of control (and
evidence) passing from one Azure AI Foundry agent to another within a
workflow run. Field names use this codebase's snake_case convention
(``source_agent_id`` etc.); they correspond 1:1 to the spec's
``sourceAgentId``/``targetAgentId``/... field names.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["AgentHandoff"]


class AgentHandoff(BaseModel):
    """A single control handoff from one agent to another."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    source_agent_id: str = Field(min_length=1)
    target_agent_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    workflow_run_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    evidence_references: list[str] = Field(default_factory=list)
    timestamp: datetime
