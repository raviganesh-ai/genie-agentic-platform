"""Thin wrapper over the raw azure-ai-projects SDK's versioned Agents admin API.

This module (together with ``project_service.py``) is one of the only
places allowed to reach into the azure-ai-projects SDK - see
``tests/unit/test_architecture_boundary.py``. Everything above this layer
(``FoundryAgentProvider``, ``AzureAgentGateway``, ``CustomerAgentProvisioningService``,
the Phase 10A inventory/lifecycle/synchronization services, and every
future orchestrator/API route) depends only on the ``AgentApiClient``
protocol below, never on the SDK types directly.

VERIFIED against the installed SDK (azure-ai-projects==2.3.0, which now
ships ``client.agents`` as the *versioned* Foundry Agents admin surface -
``azure.ai.projects.operations.AgentsOperations`` - confirmed via
``inspect.signature``): agents are addressed by a human-readable
``agent_name`` (chosen by the caller, not server-generated) and are
immutable per version; every update creates a new version under the same
name.

    client.agents.get(agent_name) -> AgentDetails (raises a not-found
        style exception, normalized to False by
        ``AzureAIProjectsApiClient.agent_exists``, if no such resource
        exists). ``AgentDetails.versions.latest.version`` names the most
        recently published (non-draft) version.
    client.agents.create_version(agent_name, *, definition=PromptAgentDefinition(
        kind="prompt", model=..., instructions=...)) -> AgentVersionDetails
        (used exclusively by ``CustomerAgentProvisioningService`` to clone
        a dedicated, per-customer copy of an already-approved catalog
        agent - never to invent new agent reasoning of any kind; the
        cloned agent's instructions are always exactly the catalog agent's
        own configured description).
    client.agents.delete(agent_name, force=None) -> DeleteAgentResponse
        (deletes every version of the named agent resource).

Actual run execution (threads/messages/tool-calling) no longer goes
through this admin-plane client at all - see
``app.agents.foundry.agent_provider.FoundryAgentProvider``, which executes
runs via ``agent_framework.foundry.FoundryAgent`` instead, so this module
only needs to expose existence/version-resolution/lifecycle operations.

This preview SDK's exact method names have changed across versions and may
change again in a future release; re-run the verification above (import
``AgentsOperations`` and diff ``inspect.signature(...)``) whenever
``azure-ai-projects`` is upgraded. Every SDK call is isolated to
``AzureAIProjectsApiClient`` below so any drift only needs to be
reconciled in this one class.
"""
from __future__ import annotations

from typing import Any, Protocol

from azure.ai.projects.models import PromptAgentDefinition


class AgentApiClient(Protocol):
    """The minimal, low-level Foundry admin operations Genie depends on.

    Isolating this behind a protocol means nothing above this module ever
    references the azure-ai-projects SDK's actual types.
    """

    def agent_exists(self, agent_id: str) -> bool:
        """Return True if a Foundry agent resource named ``agent_id`` exists.

        Used only for pre-execution synchronization checks (see
        ``FoundryAgentSynchronizationService``) and to resolve whether a
        catalog agent still needs provisioning, never for run execution
        itself.
        """
        ...

    def get_latest_version(self, agent_id: str) -> str:
        """Return the latest published version identifier for an agent resource.

        Used by ``FoundryAgentProvider`` to resolve a concrete version to
        execute against when ``AgentDefinition.foundry_agent_version`` is
        not pinned.
        """
        ...

    def create_agent(self, *, name: str, model: str, instructions: str) -> str:
        """Create a new Foundry Prompt Agent version and return its agent_name.

        Used exclusively by ``CustomerAgentProvisioningService`` to clone a
        dedicated, per-customer copy of an already-approved catalog agent.
        """
        ...

    def delete_agent(self, agent_id: str) -> None:
        """Delete every version of a Foundry agent resource previously created by ``create_agent``.

        Used exclusively by ``CustomerAgentProvisioningService`` to tear down
        a customer's dedicated agents on explicit session close.
        """
        ...


class AzureAIProjectsApiClient:
    """Concrete ``AgentApiClient`` backed by ``azure.ai.projects``'s ``AgentsOperations``."""

    def __init__(self, sdk_client: Any) -> None:
        self._sdk_client = sdk_client

    def agent_exists(self, agent_id: str) -> bool:
        try:
            self._sdk_client.agents.get(agent_id)
        except Exception:  # noqa: BLE001 - any lookup failure means "not verified"
            # Any lookup failure (not-found, transient network error, etc.)
            # is treated as "not verified" here; the caller
            # (FoundryAgentSynchronizationService) is responsible for
            # deciding whether that should fail startup closed.
            return False
        return True

    def get_latest_version(self, agent_id: str) -> str:
        details = self._sdk_client.agents.get(agent_id)
        return details.versions.latest.version

    def create_agent(self, *, name: str, model: str, instructions: str) -> str:
        definition = PromptAgentDefinition(kind="prompt", model=model, instructions=instructions)
        version_details = self._sdk_client.agents.create_version(name, definition=definition)
        return version_details.name

    def delete_agent(self, agent_id: str) -> None:
        self._sdk_client.agents.delete(agent_id)

