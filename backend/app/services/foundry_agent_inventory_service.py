"""Foundry agent inventory service (Phase 10A).

Maintains a queryable, in-process inventory of every configured agent's
Foundry deployment identity and current synchronization/validation/
lifecycle status. Deliberately holds its state in-process (an
``asyncio.Lock``-guarded dict), the same pattern already established by
``DecisionGraphService`` (Phase 6) - a durable backend can replace this
later without changing any caller, per the Memory/Governance precedent of
isolating storage behind a service.

    config/agents/*.yaml -> AgentRegistry -> FoundryAgentInventoryService
        -> FoundryAgentSynchronizationService / FoundryAgentLifecycleService
        -> queried by foundry_admin API routes and export tooling.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.agents.models import AgentDefinition
from app.models.agent_lifecycle_state import AgentLifecycleState
from app.models.foundry_agent_inventory_record import FoundryAgentInventoryRecord
from app.models.synchronization_result import SynchronizationResult, ValidationStatus

__all__ = ["FoundryAgentInventoryService", "UnknownInventoryAgentError"]


class UnknownInventoryAgentError(RuntimeError):
    """Raised when an inventory operation references an agent id never seeded."""


class FoundryAgentInventoryService:
    """In-process, lock-guarded store of ``FoundryAgentInventoryRecord`` entries."""

    def __init__(self) -> None:
        self._records: dict[str, FoundryAgentInventoryRecord] = {}
        self._lock = asyncio.Lock()

    async def seed_from_agent(self, agent: AgentDefinition) -> FoundryAgentInventoryRecord:
        """Create (or return the existing) baseline inventory record for ``agent``.

        Never overwrites an existing record's mutable status fields - only
        called once per agent, typically during provisioning/registration.
        """

        async with self._lock:
            existing = self._records.get(agent.id)
            if existing is not None:
                return existing

            record = FoundryAgentInventoryRecord(
                agent_id=agent.id,
                display_name=agent.name,
                version=agent.version,
                owner=agent.owner,
                foundry_agent_reference=agent.foundry_agent_id,
                governance_policy_id=agent.governance_policy_id,
                prompt_template_ref=agent.prompt_template_ref,
                model_deployment_ref=agent.model_deployment_ref,
                memory_scope=list(agent.memory_access),
                lifecycle_state="draft",
                synchronization_status="provisioning_required",
                validation_status="not_validated",
            )
            self._records[agent.id] = record
            return record

    async def record_synchronization_result(self, result: SynchronizationResult) -> None:
        async with self._lock:
            record = self._require(result.agent_id)
            self._records[result.agent_id] = record.model_copy(
                update={
                    "synchronization_status": result.status,
                    "last_synchronization_time": result.synchronized_at,
                }
            )

    async def record_validation_status(
        self, agent_id: str, status: ValidationStatus, *, validated_at: datetime | None = None
    ) -> None:
        async with self._lock:
            record = self._require(agent_id)
            self._records[agent_id] = record.model_copy(
                update={
                    "validation_status": status,
                    "last_validation_time": validated_at or datetime.now(UTC),
                }
            )

    async def record_lifecycle_state(self, agent_id: str, state: AgentLifecycleState) -> None:
        async with self._lock:
            record = self._require(agent_id)
            self._records[agent_id] = record.model_copy(update={"lifecycle_state": state})

    async def get(self, agent_id: str) -> FoundryAgentInventoryRecord:
        async with self._lock:
            return self._require(agent_id)

    async def list(self) -> list[FoundryAgentInventoryRecord]:
        async with self._lock:
            return list(self._records.values())

    def _require(self, agent_id: str) -> FoundryAgentInventoryRecord:
        try:
            return self._records[agent_id]
        except KeyError as exc:
            raise UnknownInventoryAgentError(
                f"No inventory record has been seeded for agent '{agent_id}'."
            ) from exc
