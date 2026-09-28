"""Owns the Azure AI Foundry project connection (endpoint + credential).

The only other module permitted to import ``azure.ai.projects`` /
``azure.identity`` is ``api_client.py``; this module is responsible for
*constructing* those SDK clients (using Microsoft Entra ID / managed
identity per the Security Requirements in
``.github/copilot-instructions.md``) and handing back the ``AgentApiClient``
abstraction (for admin-plane operations - existence checks, per-customer
agent create/delete) or the raw async ``AIProjectClient`` (for
``agent_framework.foundry.FoundryAgent`` to execute runs against - see
``app.agents.foundry.agent_provider``), never a credential or endpoint
string directly.
"""
from __future__ import annotations

from typing import Any

from app.agents.foundry.api_client import AgentApiClient, AzureAIProjectsApiClient
from app.agents.foundry.errors import FoundryUnavailableError


class FoundryProjectService:
    """Lazily constructs and caches the Azure AI Foundry project client(s).

    Credentials are obtained via ``DefaultAzureCredential``, which resolves
    to managed identity in Azure and to developer credentials locally -
    never a hardcoded key/secret (see Security Requirements). Construction
    failures (missing SDK, invalid endpoint, credential failure) are
    normalized to ``FoundryUnavailableError`` so callers fail closed
    instead of crashing with an SDK-specific exception type.
    """

    def __init__(self, *, endpoint: str, project_name: str) -> None:
        if not endpoint.strip():
            raise FoundryUnavailableError("Azure AI Foundry endpoint must not be blank.")
        if not project_name.strip():
            raise FoundryUnavailableError("Azure AI Foundry project name must not be blank.")
        self._endpoint = endpoint
        self._project_name = project_name
        self._api_client: AgentApiClient | None = None
        self._async_project_client: Any | None = None

    @property
    def project_name(self) -> str:
        return self._project_name

    def get_api_client(self) -> AgentApiClient:
        """Return the cached ``AgentApiClient``, constructing it on first use."""

        if self._api_client is not None:
            return self._api_client

        try:
            from azure.ai.projects import AIProjectClient
            from azure.identity import DefaultAzureCredential
        except ImportError as exc:
            raise FoundryUnavailableError(
                "azure-ai-projects / azure-identity are not installed; cannot "
                "reach Azure AI Foundry."
            ) from exc

        try:
            sdk_client = AIProjectClient(endpoint=self._endpoint, credential=DefaultAzureCredential())
        except Exception as exc:  # normalize every SDK failure mode to FoundryUnavailableError
            raise FoundryUnavailableError(
                f"Failed to construct Azure AI Foundry client for endpoint "
                f"'{self._endpoint}': {exc}"
            ) from exc

        self._api_client = AzureAIProjectsApiClient(sdk_client)
        return self._api_client

    def get_async_project_client(self) -> Any:
        """Return the cached async ``AIProjectClient``, constructing it on first use.

        Distinct from ``get_api_client()``'s synchronous client: this one is
        handed directly to ``agent_framework.foundry.FoundryAgent`` (an
        async-native execution API), never wrapped in ``AgentApiClient`` -
        the only caller is ``FoundryAgentProvider``.
        """

        if self._async_project_client is not None:
            return self._async_project_client

        try:
            from azure.ai.projects.aio import AIProjectClient
            from azure.identity import DefaultAzureCredential
        except ImportError as exc:
            raise FoundryUnavailableError(
                "azure-ai-projects / azure-identity are not installed; cannot "
                "reach Azure AI Foundry."
            ) from exc

        try:
            self._async_project_client = AIProjectClient(
                endpoint=self._endpoint, credential=DefaultAzureCredential()
            )
        except Exception as exc:  # normalize every SDK failure mode to FoundryUnavailableError
            raise FoundryUnavailableError(
                f"Failed to construct Azure AI Foundry async client for endpoint "
                f"'{self._endpoint}': {exc}"
            ) from exc

        return self._async_project_client

