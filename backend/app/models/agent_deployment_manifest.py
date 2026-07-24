"""Agent deployment manifest domain model (Phase 10A).

Produced by ``FoundryAgentProvisioningService`` when an agent is first
registered/provisioned - a complete, point-in-time declaration of what a
Foundry agent deployment *should* look like, used as the baseline
``FoundryAgentDriftValidator`` compares later synchronization runs against.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.agent_lifecycle_state import AgentLifecycleState
from app.models.synchronization_result import SynchronizationStatus, ValidationStatus

__all__ = ["AgentDeploymentManifest"]


class AgentDeploymentManifest(BaseModel):
    """A single agent's complete, point-in-time deployment declaration."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    foundry_agent_reference: str = Field(min_length=1)
    governance_policy_id: str = Field(min_length=1)
    prompt_template_ref: str | None = None
    model_deployment_ref: str | None = None
    memory_scope: list[str] = Field(default_factory=list)
    lifecycle_state: AgentLifecycleState
    synchronization_status: SynchronizationStatus
    validation_status: ValidationStatus
    deployment_metadata: dict[str, str] = Field(default_factory=dict)
    generated_at: datetime
