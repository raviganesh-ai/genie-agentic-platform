"""Verifies every configured ``foundry_agent_id`` exists in Azure AI Foundry.

    config/agents/*.yaml
        -> AgentRegistry (loaded, Phase 2)
        -> FoundryAgentSynchronizationService.synchronize()
        -> Azure AI Foundry (AgentApiClient.agent_exists)
        -> foundryAgentReference verified
        -> startup allowed

This closes the gap between "the YAML file names a foundry_agent_id" and
"that Azure AI Foundry agent resource actually exists and is reachable".
Genie never creates, updates, or deletes Foundry agent resources here - it
only reads/verifies, per the Production Agent Rules in
``.github/copilot-instructions.md`` (every business/debugging agent is
provisioned independently in Foundry - portal/CLI/IaC - never created ad
hoc by Genie code).
"""
from __future__ import annotations

from app.agents.foundry.errors import FoundryAgentSynchronizationError, FoundryUnavailableError
from app.agents.foundry.project_service import FoundryProjectService
from app.agents.registry import AgentRegistry

__all__ = ["FoundryAgentSynchronizationService"]


class FoundryAgentSynchronizationService:
    """Fails closed if any enabled agent's ``foundry_agent_id`` cannot be verified.

    Agents with no ``foundry_agent_id`` (local-development-only agents) are
    skipped - they can never be executed through ``AzureAgentGateway``
    anyway (see ``AzureAgentGateway.execute``). Disabled agents are skipped
    too, since they cannot be executed at all.
    """

    def __init__(self, project_service: FoundryProjectService) -> None:
        self._project_service = project_service

    def synchronize(self, agent_registry: AgentRegistry) -> None:
        """Verify every enabled agent with a ``foundry_agent_id`` exists in Foundry.

        Raises ``FoundryAgentSynchronizationError`` (fail closed) if Foundry
        itself cannot be reached, or if any referenced agent resource cannot
        be verified to exist.
        """

        try:
            client = self._project_service.get_api_client()
        except FoundryUnavailableError as exc:
            raise FoundryAgentSynchronizationError(
                f"Cannot synchronize agent registry with Azure AI Foundry: {exc}"
            ) from exc

        unverified: list[str] = []
        for agent in agent_registry.list():
            if not agent.enabled or not agent.foundry_agent_id:
                continue

            try:
                exists = client.agent_exists(agent.foundry_agent_id)
            except FoundryUnavailableError as exc:
                unverified.append(
                    f"'{agent.id}' (foundry_agent_id='{agent.foundry_agent_id}'): {exc}"
                )
                continue

            if not exists:
                unverified.append(
                    f"'{agent.id}' (foundry_agent_id='{agent.foundry_agent_id}'): no "
                    f"matching agent resource found in Azure AI Foundry."
                )

        if unverified:
            raise FoundryAgentSynchronizationError(
                "Foundry agent synchronization failed for: " + "; ".join(unverified)
            )
