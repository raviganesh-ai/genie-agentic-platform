"""Foundry agent inventory record domain model (Phase 10A).

A single, queryable snapshot of everything Genie knows about one
configured agent's Foundry deployment: identity, ownership, governance and
prompt references, and its current synchronization/validation/lifecycle
status. Populated and updated exclusively by ``FoundryAgentInventoryService``.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.agent_lifecycle_state import AgentLifecycleState
from app.models.synchronization_result import SynchronizationStatus, ValidationStatus

__all__ = ["FoundryAgentInventoryRecord"]


class FoundryAgentInventoryRecord(BaseModel):
    """Operational inventory record for a single configured Foundry agent."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    owner: str | None = None
    foundry_agent_reference: str | None = None
    governance_policy_id: str | None = None
    prompt_template_ref: str | None = None
    model_deployment_ref: str | None = None
    memory_scope: list[str] = Field(default_factory=list)
    lifecycle_state: AgentLifecycleState = "draft"
    synchronization_status: SynchronizationStatus = "provisioning_required"
    validation_status: ValidationStatus = "not_validated"
    last_validation_time: datetime | None = None
    last_synchronization_time: datetime | None = None
