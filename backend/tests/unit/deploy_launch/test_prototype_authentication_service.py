from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from app.config.settings import Settings
from app.deploy_launch.prototype_authentication_service import (
    NullPrototypeAuthenticationService,
    PrototypeAuthenticationConfiguration,
    PrototypeAuthenticationError,
    PrototypeAuthenticationService,
    create_prototype_authentication_service,
)


class _Credential:
    def __init__(self) -> None:
        self.scopes: list[str] = []

    async def get_token(self, *scopes: str) -> SimpleNamespace:
        self.scopes.extend(scopes)
        return SimpleNamespace(token="short-lived-token")


@pytest.mark.asyncio
async def test_provisions_unique_api_spa_and_assigns_test_identity_app_role() -> None:
    requests: list[tuple[str, str, dict | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        requests.append((request.method, str(request.url), body))
        if request.method == "POST" and request.url.path.endswith("/applications"):
            return httpx.Response(201, json={"id": "app-object", "appId": "prototype-client"})
        if request.method == "POST" and request.url.path.endswith("/servicePrincipals"):
            return httpx.Response(201, json={"id": "prototype-sp"})
        if request.method == "GET" and request.url.path.endswith("/servicePrincipals"):
            return httpx.Response(
                200, json={"value": [{"id": "genie-sp", "appId": "genie-client"}]}
            )
        if request.method == "POST" and request.url.path.endswith("/appRoleAssignments"):
            return httpx.Response(201, json={"id": "assignment"})
        return httpx.Response(204)

    credential = _Credential()
    service = PrototypeAuthenticationService(
        tenant_id="tenant-1",
        test_principal_client_id="genie-client",
        credential=credential,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    configuration = await service.provision(mission_slug="claims-review-1234")
    await service.configure_frontend_redirect(
        configuration, frontend_url="https://claims-review.example.com"
    )
    test_token = await service.get_test_access_token(configuration)
    await service.delete(configuration)

    create_body = requests[0][2]
    assert create_body is not None
    assert create_body["signInAudience"] == "AzureADMyOrg"
    assert create_body["api"]["oauth2PermissionScopes"][0]["value"] == "access_as_user"
    assert create_body["appRoles"][0]["value"] == "Prototype.Invoke"
    assert create_body["optionalClaims"]["accessToken"][0]["name"] == "idtyp"
    assert configuration.client_id == "prototype-client"
    assert configuration.delegated_scope == "api://prototype-client/access_as_user"
    assert any(
        body == {"identifierUris": ["api://prototype-client"]}
        for _, _, body in requests
    )
    assert any(
        body == {
            "principalId": "genie-sp",
            "resourceId": "prototype-sp",
            "appRoleId": configuration.application_role_id,
        }
        for _, _, body in requests
    )
    assert any(
        body == {"spa": {"redirectUris": ["https://claims-review.example.com/"]}}
        for _, _, body in requests
    )
    assert test_token == "short-lived-token"
    assert "api://prototype-client/.default" in credential.scopes
    assert any(
        method == "DELETE" and url.endswith("/applications/app-object")
        for method, url, _ in requests
    )


@pytest.mark.asyncio
async def test_rolls_back_application_when_service_principal_creation_fails() -> None:
    deleted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/applications"):
            return httpx.Response(201, json={"id": "app-object", "appId": "prototype-client"})
        if request.method == "POST" and request.url.path.endswith("/servicePrincipals"):
            return httpx.Response(403, headers={"request-id": "request-1"})
        if request.method == "DELETE":
            deleted.append(request.url.path)
            return httpx.Response(204)
        return httpx.Response(204)

    service = PrototypeAuthenticationService(
        tenant_id="tenant-1",
        test_principal_client_id="genie-client",
        credential=_Credential(),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(PrototypeAuthenticationError, match="403"):
        await service.provision(mission_slug="claims-review-1234")

    assert deleted == ["/v1.0/applications/app-object"]


@pytest.mark.asyncio
async def test_delete_retries_transient_graph_failures(monkeypatch) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503)
        return httpx.Response(204)

    async def no_delay(_: float) -> None:
        return None

    monkeypatch.setattr(
        "app.deploy_launch.prototype_authentication_service.asyncio.sleep", no_delay
    )
    service = PrototypeAuthenticationService(
        tenant_id="tenant-1",
        test_principal_client_id="genie-client",
        credential=_Credential(),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    configuration = PrototypeAuthenticationConfiguration(
        application_object_id="app-object",
        service_principal_object_id="prototype-sp",
        client_id="prototype-client",
        tenant_id="tenant-1",
        delegated_scope="api://prototype-client/access_as_user",
        application_role_id="role-1",
    )

    await service.delete(configuration)

    assert attempts == 3


@pytest.mark.asyncio
async def test_shared_registration_accepts_only_pre_registered_slot_urls() -> None:
    requests: list[tuple[str, dict | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        requests.append((request.method, body))
        raise AssertionError(f"Unexpected Graph request: {request.method}")

    service = PrototypeAuthenticationService(
        tenant_id="tenant-1",
        test_principal_client_id="genie-client",
        credential=_Credential(),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        shared_configuration=PrototypeAuthenticationConfiguration(
            application_object_id="shared-app-object",
            service_principal_object_id="shared-sp",
            client_id="shared-client",
            tenant_id="tenant-1",
            delegated_scope="api://shared-client/access_as_user",
            application_role_id="shared-role",
            shared=True,
        ),
        shared_frontend_domain="gentleisland.example.com",
        shared_slot_count=50,
    )

    configuration = await service.provision(mission_slug="claims-review-1234")
    await service.configure_frontend_redirect(
        configuration,
        frontend_url=(
            "https://genie-prototype-001-frontend.gentleisland.example.com"
        ),
    )
    await service.delete(configuration)

    assert configuration.shared is True
    assert configuration.mission_slug == "claims-review-1234"
    assert configuration.frontend_redirect_uri == (
        "https://genie-prototype-001-frontend.gentleisland.example.com/"
    )
    assert requests == []

    with pytest.raises(PrototypeAuthenticationError, match="pre-registered"):
        await service.configure_frontend_redirect(
            configuration, frontend_url="https://unregistered.example.com"
        )


def test_factory_is_explicitly_disabled_or_fails_closed_when_enabled() -> None:
    disabled = create_prototype_authentication_service(settings=Settings())
    assert isinstance(disabled, NullPrototypeAuthenticationService)

    with pytest.raises(
        PrototypeAuthenticationError, match="prototype_test_principal_client_id"
    ):
        create_prototype_authentication_service(
            settings=Settings(
                prototype_api_gateway_enabled=True,
                entra_tenant_id="tenant-1",
            )
        )


def test_factory_shared_mode_does_not_require_cross_tenant_test_principal() -> None:
    service = create_prototype_authentication_service(
        settings=Settings(
            prototype_api_gateway_enabled=True,
            prototype_authentication_mode="shared",
            entra_tenant_id="corporate-tenant",
            prototype_shared_application_object_id="application-object",
            prototype_shared_service_principal_object_id="service-principal",
            prototype_shared_client_id="client-id",
            prototype_shared_delegated_scope="api://client-id/access_as_user",
            prototype_shared_application_role_id="role-id",
            prototype_shared_frontend_domain="prototype.example.com",
            prototype_shared_slot_count=50,
        )
    )

    assert isinstance(service, PrototypeAuthenticationService)
    assert service._credential is None
