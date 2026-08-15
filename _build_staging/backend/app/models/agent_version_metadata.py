"""Agent version metadata domain model (Phase 10A).

A minimal, point-in-time record of the version/deployment identity Genie
observed for one agent during a synchronization run - used by
``FoundryAgentDriftValidator``/``FoundryAgentSynchronizationService`` to
detect version and deployment drift over time.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["AgentVersionMetadata"]


class AgentVersionMetadata(BaseModel):
    """A single observed (agent version, deployment reference) pair."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    model_deployment_ref: str | None = None
    foundry_agent_reference: str | None = None
    recorded_at: datetime
