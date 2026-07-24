"""Thin wrapper over the raw azure-ai-projects SDK "agents" operations.

This module (together with ``project_service.py``) is the only place that
imports ``azure.ai.projects`` / ``azure.identity``. Everything above this
layer (``FoundryAgentProvider``, ``AzureAgentGateway``, and every future
orchestrator/API route) depends only on the ``AgentApiClient`` protocol
below, never on the SDK types directly.

ASSUMPTION (verify against the installed azure-ai-projects version before
relying on this in production): the Azure AI Foundry Agent Service exposes
thread/message/run operations under ``AIProjectClient(endpoint=...,
credential=...).agents``, addressing an *existing* agent resource by id
(agents are provisioned independently - e.g. via Foundry portal/CLI/IaC -
never created ad hoc by this backend):

    client.agents.threads.create() -> object with `.id`
    client.agents.messages.create(thread_id, role="user", content=...) -> None
    client.agents.runs.create(thread_id, agent_id=...) -> object with `.id`, `.status`
    client.agents.runs.get(thread_id, run_id) -> object with `.id`, `.status`
    client.agents.messages.list(thread_id) -> iterable of message objects
    client.agents.get_agent(agent_id) -> object with `.id` (raises a
        not-found style exception, normalized to False by
        ``AzureAIProjectsApiClient.agent_exists``, if no such resource
        exists)

This preview SDK's exact method names have changed across versions. Every
SDK call is isolated to ``AzureAIProjectsApiClient`` below so any drift only
needs to be reconciled in this one class.
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
