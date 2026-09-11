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
    ContainerAppFrontendDeploymentService,
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


async def test_protected_backend_deploys_private_backend_without_mise_sidecar(
    monkeypatch, tmp_path
):
    from azure.storage.blob import BlobClient

    (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    gateway_calls = {}

    class FakeGatewayService:
        async def provision_infrastructure(self, *, mission_slug, on_progress=None):
            gateway_calls["provision"] = mission_slug
            return SimpleNamespace(managed_environment_id="private-env-123")

        async def publish_api(
            self,
            *,
            mission_slug,
            backend_url,
            on_progress=None,
        ):
            gateway_calls["publish"] = (mission_slug, backend_url)
            return "https://claims-1234.azure-api.net"

    service = BackendDeploymentService(
        subscription_id="sub-123",
        resource_group="genie-dev-rg",
        acr_name="acr123",
        container_apps_environment_id="env-123",
        location="eastus2",
        foundry_endpoint="https://foundry.example.com/api/projects/demo",
        foundry_project_name="demo",
        prototype_api_gateway_service=FakeGatewayService(),
    )
    acr_client = SimpleNamespace(
        registries=SimpleNamespace(
            get_build_source_upload_url=lambda *_: SimpleNamespace(
                upload_url="https://upload.example.com/source", relative_path="source.tgz"
            ),
            begin_schedule_run=lambda *_: SimpleNamespace(
                result=lambda: SimpleNamespace(run_id="run-1")
            ),
        ),
        runs=SimpleNamespace(get=lambda *_: SimpleNamespace(status="Succeeded")),
    )
    captured = {}

    def begin_create_or_update(resource_group, app_name, envelope):
        captured["resource_group"] = resource_group
        captured["app_name"] = app_name
        captured["envelope"] = envelope
        return SimpleNamespace(
            result=lambda: SimpleNamespace(
                configuration=SimpleNamespace(
                    ingress=SimpleNamespace(fqdn="prototype.example.com")
                )
            )
        )

    container_apps_client = SimpleNamespace(
        container_apps=SimpleNamespace(begin_create_or_update=begin_create_or_update)
    )
    monkeypatch.setattr(service, "_acr_client", lambda: acr_client)
    monkeypatch.setattr(
        service,
        "_acr_credentials_client",
        lambda: pytest.fail("protected deployment must not read ACR admin credentials"),
    )
    monkeypatch.setattr(service, "_container_apps_client", lambda: container_apps_client)
    monkeypatch.setattr(service, "_configure_mission_identity", lambda *_: "mission-client")
    monkeypatch.setattr(
        BlobClient,
        "from_blob_url",
        lambda *_: SimpleNamespace(upload_blob=lambda *_args, **_kwargs: None),
    )

    result = await service.deploy(
        mission_slug="claims-1234",
        build_root=tmp_path,
        mission_identity_resource_id=(
            "/subscriptions/sub-123/resourceGroups/genie-dev-rg/providers/"
            "Microsoft.ManagedIdentity/userAssignedIdentities/claims-1234"
        ),
    )

    envelope = captured["envelope"]
    assert result.backend_url == "https://claims-1234.azure-api.net"
    assert envelope.managed_environment_id == "private-env-123"
    assert envelope.configuration.ingress.external is False
    assert envelope.configuration.ingress.target_port == 8000
    assert envelope.configuration.ingress.additional_port_mappings is None
    assert envelope.configuration.registries[0].identity.endswith("/claims-1234")
    assert envelope.configuration.registries[0].username is None
    assert envelope.configuration.secrets == []
    assert [container.name for container in envelope.template.containers] == ["backend"]
    backend = next(
        container for container in envelope.template.containers if container.name == "backend"
    )
    backend_environment = {item.name: item.value for item in backend.env}
    assert "ENTRA_CLIENT_ID" not in backend_environment
    assert "ENTRA_TENANT_ID" not in backend_environment
    assert "GENIE_ACCEPTANCE_TEST_KEY" not in backend_environment
    assert gateway_calls["provision"] == "claims-1234"
    assert gateway_calls["publish"] == (
        "claims-1234",
        "https://prototype.example.com",
    )


async def test_frontend_deployment_uses_mission_identity_for_acr(monkeypatch, tmp_path):
    from azure.storage.blob import BlobClient

    (tmp_path / "package.json").write_text("{}\n", encoding="utf-8")
    service = ContainerAppFrontendDeploymentService(
        subscription_id="sub-123",
        resource_group="genie-dev-rg",
        acr_name="acr123",
        container_apps_environment_id="env-123",
        location="eastus2",
    )
    acr_client = SimpleNamespace(
        registries=SimpleNamespace(
            get_build_source_upload_url=lambda *_: SimpleNamespace(
                upload_url="https://upload.example.com/source", relative_path="source.tgz"
            ),
            begin_schedule_run=lambda *_: SimpleNamespace(
                result=lambda: SimpleNamespace(run_id="run-1")
            ),
        ),
        runs=SimpleNamespace(get=lambda *_: SimpleNamespace(status="Succeeded")),
    )
    captured = {}

    def begin_create_or_update(resource_group, app_name, envelope):
        captured["envelope"] = envelope
        return SimpleNamespace(
            result=lambda: SimpleNamespace(
                configuration=SimpleNamespace(
                    ingress=SimpleNamespace(fqdn="frontend.example.com")
                )
            )
        )

    monkeypatch.setattr(service, "_acr_client", lambda: acr_client)
    monkeypatch.setattr(
        service,
        "_container_apps_client",
        lambda: SimpleNamespace(
            container_apps=SimpleNamespace(begin_create_or_update=begin_create_or_update)
        ),
    )
    monkeypatch.setattr(
        BlobClient,
        "from_blob_url",
        lambda *_: SimpleNamespace(upload_blob=lambda *_args, **_kwargs: None),
    )
    mission_identity_resource_id = (
        "/subscriptions/sub-123/resourceGroups/genie-dev-rg/providers/"
        "Microsoft.ManagedIdentity/userAssignedIdentities/claims-1234"
    )

    result = await service.deploy(
        mission_slug="claims-1234",
        ui_root=tmp_path,
        mission_identity_resource_id=mission_identity_resource_id,
    )

    envelope = captured["envelope"]
    assert result.frontend_url == "https://frontend.example.com"
    assert mission_identity_resource_id in envelope.identity.user_assigned_identities
    assert envelope.configuration.registries[0].identity == mission_identity_resource_id
    assert envelope.configuration.registries[0].username is None
    assert envelope.configuration.secrets == []


async def test_gateway_origin_finalization_updates_apim_policy():
    calls = []

    class FakeGatewayService:
        async def configure_frontend_origin(self, **kwargs):
            calls.append(kwargs)

    service = BackendDeploymentService(
        subscription_id="sub-123",
        resource_group="genie-dev-rg",
        acr_name="acr123",
        container_apps_environment_id="env-123",
        location="eastus2",
        foundry_endpoint="https://foundry.example.com/api/projects/demo",
        foundry_project_name="demo",
        prototype_api_gateway_service=FakeGatewayService(),
    )

    await service.configure_gateway_frontend_origin(
        mission_slug="claims-1234",
        frontend_origin="https://prototype.example.com/",
    )

    assert calls == [
        {
            "mission_slug": "claims-1234",
            "frontend_origin": "https://prototype.example.com/",
        }
    ]
