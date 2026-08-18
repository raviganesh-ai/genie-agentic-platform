"""Unit tests for BackendDeploymentService's factory (real vs Null selection)."""
from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from app.config.settings import Settings
from app.deploy_launch.backend_deployment_service import (
    BackendDeploymentError,
    BackendDeploymentService,
    NullBackendDeploymentService,
    create_backend_deployment_service,
)
from app.deploy_launch.container_app_frontend_deployment_service import (
    ContainerAppFrontendDeploymentError,
    create_container_app_frontend_deployment_service,
)
from app.deploy_launch.mission_identity_service import (
    MissionIdentityProvisioningError,
    create_mission_identity_service,
)


def _settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[call-arg]


async def test_missing_config_fails_closed_instead_of_selecting_null_backend():
    with pytest.raises(BackendDeploymentError, match="fake backend deployment is not permitted"):
        create_backend_deployment_service(settings=_settings())


async def test_null_service_deploy_returns_a_local_placeholder_url(tmp_path):
    service = NullBackendDeploymentService()

    result = await service.deploy(mission_slug="acme-mission", build_root=tmp_path)

    assert result.image_tag == "local/acme-mission:dev"
    assert "acme-mission" in result.backend_url


def test_deployment_config_without_foundry_fails_closed():
    """azure_foundry_endpoint/azure_foundry_project_name are required too - the
    deployed mission backend's main.py reads them at request time to reach
    its own already-provisioned Foundry orchestrator agent, so a real
    service must never be built without them."""

    with pytest.raises(BackendDeploymentError, match="fake backend deployment is not permitted"):
        create_backend_deployment_service(
            settings=_settings(
                azure_subscription_id="sub-1",
                deployment_resource_group="rg-1",
                deployment_acr_name="acr1",
                deployment_container_apps_environment_id="env-1",
                deployment_location="eastus2",
            )
        )


def test_missing_config_fails_closed_for_frontend_and_identity_factories():
    settings = _settings()

    with pytest.raises(ContainerAppFrontendDeploymentError, match="fake frontend deployment"):
        create_container_app_frontend_deployment_service(settings=settings)
    with pytest.raises(MissionIdentityProvisioningError, match="fake mission identity"):
        create_mission_identity_service(
            settings=settings,
            subscription_id="unknown",
            resource_group_name="unknown",
        )


@pytest.mark.parametrize(
    "resource_id",
    [
        "/subscriptions/sub-123/resourcegroups/genie-dev-rg/providers/Microsoft.ManagedIdentity/userAssignedIdentities/genie-mission-demo",
        "/subscriptions/sub-123/resourceGroups/genie-dev-rg/providers/Microsoft.ManagedIdentity/userAssignedIdentities/genie-mission-demo",
        "/subscriptions/sub-123/resourceGroups/genie-dev-rg/providers/Microsoft.ManagedIdentity/UserAssignedIdentities/genie-mission-demo",
    ],
)
def test_configure_mission_identity_accepts_valid_resource_id_casing(monkeypatch, resource_id):
    """Azure resource IDs are case-insensitive for ARM path segments; real
    mission identity lookups must accept all valid casing variants."""

    service = BackendDeploymentService(
        subscription_id="sub-123",
        resource_group="genie-dev-rg",
        acr_name="acr123",
        container_apps_environment_id="env-123",
        location="eastus2",
        foundry_endpoint="https://genie-demo-resource.services.ai.azure.com/api/projects/demo",
        foundry_project_name="demo",
    )

    fake_credential = object()
    fake_identity = SimpleNamespace(principal_id="principal-123", client_id="client-123")
    assignment_calls = []

    fake_authz = SimpleNamespace(
        role_assignments=SimpleNamespace(
            create=lambda **kwargs: assignment_calls.append(kwargs) or None
        )
    )
    fake_msi = SimpleNamespace(
        user_assigned_identities=SimpleNamespace(
            get=lambda resource_group_name, identity_name: fake_identity
        )
    )

    fake_azure_identity = ModuleType("azure.identity")
    fake_azure_identity.DefaultAzureCredential = lambda: fake_credential
    fake_azure_authz = ModuleType("azure.mgmt.authorization")
    fake_azure_authz.AuthorizationManagementClient = lambda credential, subscription_id: fake_authz
    fake_azure_msi = ModuleType("azure.mgmt.msi")
    fake_azure_msi.ManagedServiceIdentityClient = lambda credential, subscription_id: fake_msi

    monkeypatch.setitem(sys.modules, "azure.identity", fake_azure_identity)
    monkeypatch.setitem(sys.modules, "azure.mgmt.authorization", fake_azure_authz)
    monkeypatch.setitem(sys.modules, "azure.mgmt.msi", fake_azure_msi)

    result = service._configure_mission_identity(resource_id)

    assert result == "client-123"
    assert assignment_calls
