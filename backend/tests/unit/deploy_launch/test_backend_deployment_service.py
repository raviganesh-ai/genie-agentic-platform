"""Unit tests for BackendDeploymentService's factory (real vs Null selection)."""
from __future__ import annotations

import pytest

from app.config.settings import Settings
from app.deploy_launch.backend_deployment_service import (
    BackendDeploymentError,
    NullBackendDeploymentService,
    create_backend_deployment_service,
)


def _settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[call-arg]


async def test_local_mode_without_config_returns_null_service():
    service = create_backend_deployment_service(settings=_settings(provider_mode="local"))

    assert isinstance(service, NullBackendDeploymentService)


async def test_null_service_deploy_returns_a_local_placeholder_url(tmp_path):
    service = NullBackendDeploymentService()

    result = await service.deploy(mission_slug="acme-mission", build_root=tmp_path)

    assert result.image_tag == "local/acme-mission:dev"
    assert "acme-mission" in result.backend_url


def test_production_mode_without_config_raises():
    with pytest.raises(BackendDeploymentError):
        create_backend_deployment_service(settings=_settings(provider_mode="production"))


def test_local_mode_with_deployment_config_but_no_foundry_config_returns_null_service():
    """azure_foundry_endpoint/azure_foundry_project_name are required too - the
    deployed mission backend's main.py reads them at request time to reach
    its own already-provisioned Foundry orchestrator agent, so a real
    service must never be built without them."""

    service = create_backend_deployment_service(
        settings=_settings(
            provider_mode="local",
            azure_subscription_id="sub-1",
            deployment_resource_group="rg-1",
            deployment_acr_name="acr1",
            deployment_container_apps_environment_id="env-1",
            deployment_location="eastus2",
        )
    )

    assert isinstance(service, NullBackendDeploymentService)


def test_production_mode_with_deployment_config_but_no_foundry_config_raises():
    with pytest.raises(BackendDeploymentError):
        create_backend_deployment_service(
            settings=_settings(
                provider_mode="production",
                azure_subscription_id="sub-1",
                deployment_resource_group="rg-1",
                deployment_acr_name="acr1",
                deployment_container_apps_environment_id="env-1",
                deployment_location="eastus2",
            )
        )
