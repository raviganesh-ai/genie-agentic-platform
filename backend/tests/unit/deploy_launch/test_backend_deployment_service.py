"""Unit tests for BackendDeploymentService's factory (real vs Null selection)."""
from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

from app.config.settings import Settings
from app.deploy_launch.backend_deployment_service import (
    BackendDeploymentService,
    NullBackendDeploymentService,
    create_backend_deployment_service,
)


def _settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[call-arg]


async def test_local_mode_without_config_returns_null_service():
    service = create_backend_deployment_service(settings=_settings())

    assert isinstance(service, NullBackendDeploymentService)


async def test_null_service_deploy_returns_a_local_placeholder_url(tmp_path):
    service = NullBackendDeploymentService()

    result = await service.deploy(mission_slug="acme-mission", build_root=tmp_path)

    assert result.image_tag == "local/acme-mission:dev"
    assert "acme-mission" in result.backend_url


def test_local_mode_with_deployment_config_but_no_foundry_config_returns_null_service():
    """azure_foundry_endpoint/azure_foundry_project_name are required too - the
    deployed mission backend's main.py reads them at request time to reach
    its own already-provisioned Foundry orchestrator agent, so a real
    service must never be built without them."""

    service = create_backend_deployment_service(
        settings=_settings(
            azure_subscription_id="sub-1",
            deployment_resource_group="rg-1",
            deployment_acr_name="acr1",
            deployment_container_apps_environment_id="env-1",
            deployment_location="eastus2",
        )
    )

    assert isinstance(service, NullBackendDeploymentService)


def test_configure_mission_identity_accepts_lowercase_resource_id_segments(monkeypatch):
    """Azure resource IDs are case-insensitive for ARM path segments; the
    real mission identity lookup should accept values like
    ``resourcegroups`` and ``userassignedidentities``."""

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

    result = service._configure_mission_identity(
        "/subscriptions/sub-123/resourcegroups/genie-dev-rg/providers/Microsoft.ManagedIdentity/userAssignedIdentities/genie-mission-demo"
    )

    assert result == "client-123"
    assert assignment_calls
