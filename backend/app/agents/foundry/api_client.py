"""Thin wrapper over the raw azure-ai-projects SDK "agents" operations.

This module (together with ``project_service.py``) is the only place that
imports ``azure.ai.projects`` / ``azure.identity``. Everything above this
layer (``FoundryAgentProvider``, ``AzureAgentGateway``, and every future
orchestrator/API route) depends only on the ``AgentApiClient`` protocol
below, never on the SDK types directly.

VERIFIED against the installed SDK (azure-ai-projects==1.1.0b4, which pulls
in azure-ai-agents as a direct dependency - confirmed via
``pip show azure-ai-projects``): the Azure AI Foundry Agent Service exposes
thread/message/run operations under ``AIProjectClient(endpoint=...,
credential=...).agents`` (a lazily-constructed ``azure.ai.agents.
AgentsClient``), addressing an *existing* agent resource by id. Every
method name and keyword-argument signature below was checked with
``inspect.signature`` against the real ``AgentsClient``/``ThreadsOperations``/
``MessagesOperations``/``RunsOperations`` classes and matches exactly,
including ``create_agent``/``delete_agent``/``get_agent`` being top-level
methods on ``AgentsClient`` itself (not nested under a sub-resource) and
the returned ``Agent`` model having a required ``id`` field. Every shared,
catalog-defined business agent (``AgentDefinition.foundry_agent_id``) is
provisioned independently - e.g. via Foundry portal/CLI/IaC - never created
ad hoc by this backend. The one exception is ``create_agent``/
``delete_agent`` below, used exclusively by
``CustomerAgentProvisioningService`` to clone a dedicated, per-customer
copy of an already-approved catalog agent - never to invent new agent
reasoning of any kind (the cloned agent's instructions are always exactly
the catalog agent's own configured description):

    client.agents.threads.create() -> object with `.id`
    client.agents.messages.create(thread_id, role="user", content=...) -> None
    client.agents.runs.create(thread_id, agent_id=...) -> object with `.id`, `.status`
    client.agents.runs.get(thread_id, run_id) -> object with `.id`, `.status`
    client.agents.messages.list(thread_id) -> iterable of message objects
    client.agents.get_agent(agent_id) -> object with `.id` (raises a
        not-found style exception, normalized to False by
        ``AzureAIProjectsApiClient.agent_exists``, if no such resource
        exists)
    client.agents.create_agent(model=..., name=..., instructions=...) ->
        object with `.id`
    client.agents.delete_agent(agent_id) -> None

This preview SDK's exact method names have changed across versions and may
change again in a future release; re-run the verification above (import
each operations class and diff ``inspect.signature(...)``) whenever
``azure-ai-projects``/``azure-ai-agents`` is upgraded. Every SDK call is
isolated to ``AzureAIProjectsApiClient`` below so any drift only needs to
be reconciled in this one class.
"""
from __future__ import annotations

from typing import Any, Protocol


class AgentApiClient(Protocol):
    """The minimal, low-level Foundry operations Genie depends on.

    Isolating this behind a protocol means nothing above this module ever
    references the azure-ai-projects SDK's actual types.
    """

    def create_thread(self) -> str:
        """Create a new conversation thread and return its id."""
        ...

    def create_message(self, *, thread_id: str, role: str, content: str) -> None:
        """Post a message onto an existing thread."""
        ...

    def create_run(self, *, thread_id: str, agent_id: str) -> Any:
        """Start a run of the given (pre-existing) Foundry agent on a thread."""
        ...

    def get_run(self, *, thread_id: str, run_id: str) -> Any:
        """Fetch the current status of a run."""
        ...

    def list_messages(self, *, thread_id: str) -> Any:
        """List messages on a thread, most recent first."""
        ...

    def agent_exists(self, agent_id: str) -> bool:
        """Return True if an agent resource with this id exists in Foundry.

        Used only for pre-execution synchronization checks (see
        ``FoundryAgentSynchronizationService``), never for run execution
        itself.
        """
        ...

    def create_agent(self, *, name: str, model: str, instructions: str) -> str:
        """Create a new Foundry agent resource and return its id.

        Used exclusively by ``CustomerAgentProvisioningService`` to clone a
        dedicated, per-customer copy of an already-approved catalog agent.
        """
        ...

    def delete_agent(self, agent_id: str) -> None:
        """Delete a Foundry agent resource previously created by ``create_agent``.

        Used exclusively by ``CustomerAgentProvisioningService`` to tear down
        a customer's dedicated agents on explicit session close.
        """
        ...


class AzureAIProjectsApiClient:
    """Concrete ``AgentApiClient`` backed by ``azure.ai.projects.AIProjectClient``."""

    def __init__(self, sdk_client: Any) -> None:
        self._sdk_client = sdk_client

    def create_thread(self) -> str:
        thread = self._sdk_client.agents.threads.create()
        return thread.id

    def create_message(self, *, thread_id: str, role: str, content: str) -> None:
        self._sdk_client.agents.messages.create(thread_id=thread_id, role=role, content=content)

    def create_run(self, *, thread_id: str, agent_id: str) -> Any:
        return self._sdk_client.agents.runs.create(thread_id=thread_id, agent_id=agent_id)

    def get_run(self, *, thread_id: str, run_id: str) -> Any:
        return self._sdk_client.agents.runs.get(thread_id=thread_id, run_id=run_id)

    def list_messages(self, *, thread_id: str) -> Any:
        return self._sdk_client.agents.messages.list(thread_id=thread_id)

    def agent_exists(self, agent_id: str) -> bool:
        try:
            self._sdk_client.agents.get_agent(agent_id)
        except Exception:  # noqa: BLE001 - any lookup failure means "not verified"
            # Any lookup failure (not-found, transient network error, etc.)
            # is treated as "not verified" here; the caller
            # (FoundryAgentSynchronizationService) is responsible for
            # deciding whether that should fail startup closed.
            return False
        return True

    def create_agent(self, *, name: str, model: str, instructions: str) -> str:
        agent = self._sdk_client.agents.create_agent(
            model=model, name=name, instructions=instructions
        )
        return agent.id

    def delete_agent(self, agent_id: str) -> None:
        self._sdk_client.agents.delete_agent(agent_id)
