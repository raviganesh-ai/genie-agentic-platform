"""Queries the real registration state of Azure resource providers.

Only this module (and its test doubles) may import ``azure.mgmt.resource`` /
``azure.identity`` for provider-registration lookups, mirroring the
isolation pattern used by ``app.agents.foundry.project_service`` for the
Azure AI Foundry SDK: every Azure SDK failure mode is normalized to
``ResourceProviderStatusError`` so callers fail closed instead of crashing
with an SDK-specific exception type.
"""
from __future__ import annotations

from typing import Protocol

from app.deployment.models import RegistrationState


class ResourceProviderStatusError(RuntimeError):
    """Raised when an Azure resource provider's registration state cannot be determined."""


class ResourceProviderStatusSource(Protocol):
    """Abstraction over "what is this provider namespace's registration state?".

    Isolated behind an interface (per the "create an abstraction layer" rule
    in ``.github/copilot-instructions.md`` for uncertain SDK behavior) so
    the deployment-readiness validator is testable without a real Azure
    subscription.
    """

    def get_registration_state(self, subscription_id: str, namespace: str) -> RegistrationState:
        ...


class AzureResourceManagerProviderStatusSource:
    """Looks up provider registration state via the Azure Resource Manager SDK.

    Credentials are obtained via ``DefaultAzureCredential``, which resolves
    to managed identity in Azure and to developer credentials locally -
    never a hardcoded key/secret (see Security Requirements in
    ``.github/copilot-instructions.md``).
    """

    def __init__(self) -> None:
        self._client_by_subscription: dict[str, object] = {}

    def _get_client(self, subscription_id: str):
        client = self._client_by_subscription.get(subscription_id)
        if client is not None:
            return client

        try:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.resource import ResourceManagementClient
        except ImportError as exc:
            raise ResourceProviderStatusError(
                "azure-mgmt-resource / azure-identity are not installed; cannot "
                "check Azure resource provider registration state."
            ) from exc

        try:
            client = ResourceManagementClient(
                credential=DefaultAzureCredential(),
                subscription_id=subscription_id,
            )
        except Exception as exc:  # normalize every SDK failure mode
            raise ResourceProviderStatusError(
                f"Failed to construct Azure Resource Manager client for subscription "
                f"'{subscription_id}': {exc}"
            ) from exc

        self._client_by_subscription[subscription_id] = client
        return client

    def get_registration_state(self, subscription_id: str, namespace: str) -> RegistrationState:
        if not subscription_id.strip():
            raise ResourceProviderStatusError("subscription_id must not be blank.")
        if not namespace.strip():
            raise ResourceProviderStatusError("namespace must not be blank.")

        client = self._get_client(subscription_id)
        try:
            provider = client.providers.get(namespace)
        except Exception as exc:  # normalize every SDK failure mode
            raise ResourceProviderStatusError(
                f"Failed to look up registration state for provider '{namespace}' in "
                f"subscription '{subscription_id}': {exc}"
            ) from exc

        state = getattr(provider, "registration_state", None)
        if state in ("Registered", "Registering", "NotRegistered", "Unregistering"):
            return state  # type: ignore[return-value]
        return "Unknown"
