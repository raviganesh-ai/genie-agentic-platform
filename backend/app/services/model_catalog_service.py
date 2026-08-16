"""Model catalog service.

Lists model deployments available to the current Genie backend deployment and
validates user-selected model references before a workflow run starts.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import urlparse

from app.agents.registry import AgentRegistry
from app.config.settings import Settings
from app.deployment.provider_status_source import AzureModelDeploymentSource, ModelDeploymentSource

__all__ = [
    "ModelCatalog",
    "ModelCatalogService",
    "ModelCatalogUnavailableError",
    "ModelNotAvailableError",
    "create_model_catalog_service",
]


class ModelCatalogUnavailableError(RuntimeError):
    """Raised when the backend cannot read the configured model catalog."""


class ModelNotAvailableError(RuntimeError):
    """Raised when a requested model is not present in the available catalog."""


@dataclass(frozen=True)
class ModelCatalog:
    available_models: list[str]
    default_model: str


class ModelCatalogService:
    """Resolves available model deployment refs for UI and run validation."""

    def __init__(
        self,
        *,
        settings: Settings,
        agent_registry: AgentRegistry,
        deployment_source: ModelDeploymentSource,
    ) -> None:
        self._settings = settings
        self._agent_registry = agent_registry
        self._deployment_source = deployment_source

    async def get_catalog(self) -> ModelCatalog:
        models = await self._list_available_models()
        return ModelCatalog(available_models=models, default_model=self._settings.default_llm)

    async def ensure_available(self, model_deployment_ref: str) -> None:
        candidate = model_deployment_ref.strip()
        if not candidate:
            raise ModelNotAvailableError("Model deployment reference must not be blank.")
        models = await self._list_available_models()
        if candidate not in models:
            raise ModelNotAvailableError(
                f"Model deployment '{candidate}' is not available. Available models: {models}."
            )

    async def _list_available_models(self) -> list[str]:
        # Local/test mode (or no Foundry wiring): use static configured values so
        # the UI still has a deterministic picker.
        if not self._settings.azure_foundry_endpoint:
            return self._configured_models()

        # Without ARM coordinates we cannot reliably enumerate deployments.
        if not self._settings.azure_subscription_id or not self._resource_group_name():
            return self._configured_models()

        try:
            models = await asyncio.to_thread(
                self._deployment_source.list_model_deployments,
                subscription_id=self._settings.azure_subscription_id,
                resource_group=self._resource_group_name() or "",
                account_name=self._account_name(),
            )
        except Exception as exc:
            raise ModelCatalogUnavailableError(
                f"Unable to list Foundry model deployments: {exc}"
            ) from exc

        if not models:
            raise ModelCatalogUnavailableError(
                "No model deployments were returned from the configured Foundry account."
            )
        return models

    def _configured_models(self) -> list[str]:
        models = {self._settings.default_llm}
        for agent in self._agent_registry.list():
            if agent.model_deployment_ref:
                models.add(agent.model_deployment_ref)
        return sorted(models)

    def _resource_group_name(self) -> str | None:
        value = self._settings.deployment_resource_group
        return value.strip() if value and value.strip() else None

    def _account_name(self) -> str:
        parsed = urlparse(self._settings.azure_foundry_endpoint or "")
        host = (parsed.hostname or "").strip()
        if not host:
            raise ModelCatalogUnavailableError("azure_foundry_endpoint is not a valid host URL.")
        account_name = host.split(".")[0].strip()
        if not account_name:
            raise ModelCatalogUnavailableError("Could not derive Foundry account name from endpoint.")
        return account_name

def create_model_catalog_service(*, settings: Settings, agent_registry: AgentRegistry) -> ModelCatalogService:
    return ModelCatalogService(
        settings=settings,
        agent_registry=agent_registry,
        deployment_source=AzureModelDeploymentSource(),
    )
