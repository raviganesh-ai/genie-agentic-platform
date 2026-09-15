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
string directly. Also owns ``get_mcp_access_token`` (see below), the sole
place a Microsoft Entra ID token for a secured MCP server is acquired, for
the same "only the Foundry access layer touches azure-identity" reason.
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
        # Lazily created and cached on first use by get_mcp_access_token -
        # a separate DefaultAzureCredential instance from the ones
        # get_api_client()/get_async_project_client() construct for the
        # Foundry SDK clients themselves, since it is used for a different
        # audience (a self-hosted MCP server's own Entra App Registration,
        # not Azure AI Foundry).
        self._mcp_credential: Any | None = None

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

    def get_mcp_access_token(self, client_id: str) -> str:
        """Acquires a Microsoft Entra ID access token for a secured MCP server.

        Self-hosted Azure MCP Server deployments enforce Microsoft Entra ID
        authentication on every incoming HTTP request by default (verified
        against Microsoft's own reference deployment,
        Azure-Samples/azmcp-foundry-aca-mi - see
        ``infra/modules/finops-mcp-server.bicep``). ``client_id`` is that
        server's own Entra App Registration's application (client) ID,
        used as the OAuth2 audience (``api://<client-id>``). The token is
        obtained via this same process's managed identity/developer
        credential (``DefaultAzureCredential``) - never a client secret or
        static token - kept isolated to this one Foundry-access-layer
        module per the architecture boundary enforced by
        ``tests/unit/test_architecture_boundary.py``. Only
        ``FoundryAgentProvider`` calls this (via a ``header_provider``
        passed to ``agent_framework.MCPStreamableHTTPTool``).
        """

        try:
            from azure.identity import DefaultAzureCredential
        except ImportError as exc:
            raise FoundryUnavailableError(
                "azure-identity is not installed; cannot authenticate to the MCP server."
            ) from exc

        try:
            credential = self._mcp_credential
            if credential is None:
                credential = DefaultAzureCredential()
                self._mcp_credential = credential
            token = credential.get_token(f"api://{client_id}/.default")
        except Exception as exc:
            raise FoundryUnavailableError(
                f"Failed to acquire a Microsoft Entra ID access token for MCP "
                f"server audience 'api://{client_id}': {exc}"
            ) from exc

        return token.token

