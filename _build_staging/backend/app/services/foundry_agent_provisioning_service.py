"""Foundry agent provisioning service (Phase 10A).

Registers a newly configured agent's metadata into the inventory, moves it
through the ``draft -> registered`` (and, once Foundry existence is
verified by ``FoundryAgentSynchronizationService``, ``registered ->
provisioned``) lifecycle transitions, and builds the ``AgentDeploymentManifest``
baseline used for later drift detection. Genie never creates, updates, or
deletes Foundry agent resources itself here - agents are provisioned
independently in Azure AI Foundry (portal/CLI/IaC); this service only
registers and tracks *Genie's own* record of that pre-existing resource,
per the Production Agent Rules in ``.github/copilot-instructions.md``.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.agents.models import AgentDefinition
from app.governance.governance_service import GovernanceService
from app.models.agent_deployment_manifest import AgentDeploymentManifest
from app.models.agent_lifecycle_state import AgentLifecycleState
from app.services.foundry_agent_inventory_service import FoundryAgentInventoryService
from app.services.foundry_agent_lifecycle_service import FoundryAgentLifecycleService

__all__ = ["AgentProvisioningError", "FoundryAgentProvisioningService"]


class AgentProvisioningError(RuntimeError):
    """Raised when an agent cannot be provisioned due to missing required metadata."""


class FoundryAgentProvisioningService:
    """Registers configured agents into the inventory/lifecycle tracking layer."""

    def __init__(
        self,
        *,
        inventory_service: FoundryAgentInventoryService,
        lifecycle_service: FoundryAgentLifecycleService,
        governance_service: GovernanceService,
    ) -> None:
        self._inventory_service = inventory_service
        self._lifecycle_service = lifecycle_service
        self._governance_service = governance_service

    async def register(
        self, agent: AgentDefinition, *, session_id: str, trace_id: str
    ) -> AgentDeploymentManifest:
        """Seed inventory for ``agent`` and transition it draft -> registered.

        Raises ``AgentProvisioningError`` (fail closed) if ``agent`` has no
        ``foundry_agent_id`` - an agent cannot be provisioned against Azure
        AI Foundry without naming which pre-existing resource it maps to.
        """

        if not agent.foundry_agent_id:
            raise AgentProvisioningError(
                f"Agent '{agent.id}' has no foundry_agent_id configured; cannot provision."
            )

        await self._inventory_service.seed_from_agent(agent)
        await self._governance_service.record_agent_registration(
            session_id=session_id,
            trace_id=trace_id,
            agent_id=agent.id,
            detail={"version": agent.version, "foundry_agent_id": agent.foundry_agent_id},
        )
        transition = await self._lifecycle_service.transition(
            agent_id=agent.id,
            to_state="registered",
            reason="Agent configuration registered for Foundry provisioning.",
            session_id=session_id,
            trace_id=trace_id,
        )
        return self._build_manifest(agent, transition.to_state)

    async def mark_provisioned(
        self, agent: AgentDefinition, *, session_id: str, trace_id: str
    ) -> AgentDeploymentManifest:
        """Transition ``agent`` registered -> provisioned once Foundry existence is verified."""

        transition = await self._lifecycle_service.transition(
            agent_id=agent.id,
            to_state="provisioned",
            reason="Foundry agent resource existence verified by synchronization.",
            session_id=session_id,
            trace_id=trace_id,
        )
        return self._build_manifest(agent, transition.to_state)

    def _build_manifest(
        self, agent: AgentDefinition, lifecycle_state: AgentLifecycleState
    ) -> AgentDeploymentManifest:
        return AgentDeploymentManifest(
            agent_id=agent.id,
            version=agent.version,
            owner=agent.owner or "unassigned",
            foundry_agent_reference=agent.foundry_agent_id or "",
            governance_policy_id=agent.governance_policy_id or "unassigned",
            prompt_template_ref=agent.prompt_template_ref,
            model_deployment_ref=agent.model_deployment_ref,
            memory_scope=list(agent.memory_access),
            lifecycle_state=lifecycle_state,
            synchronization_status="provisioning_required",
            validation_status="not_validated",
            generated_at=datetime.now(UTC),
        )
