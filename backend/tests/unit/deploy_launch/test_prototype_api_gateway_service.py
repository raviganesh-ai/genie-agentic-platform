from __future__ import annotations

from types import SimpleNamespace
from xml.etree import ElementTree

from app.deploy_launch.prototype_api_gateway_service import (
    PrototypeApiGatewayService,
    _build_api_policy,
)
from app.deploy_launch.prototype_authentication_service import (
    PrototypeAuthenticationConfiguration,
)


def _authentication() -> PrototypeAuthenticationConfiguration:
    return PrototypeAuthenticationConfiguration(
        application_object_id="application-object",
        service_principal_object_id="service-principal",
        client_id="prototype-client",
        tenant_id="prototype-tenant",
        delegated_scope="api://prototype-client/access_as_user",
        application_role_id="application-role",
    )


def _poller(result):
    return SimpleNamespace(result=lambda: result)


def test_api_policy_enforces_exact_origin_entra_audience_and_rate_limit():
    policy = _build_api_policy(
        authentication=_authentication(),
        frontend_origin="https://prototype.example.com/",
    )

    root = ElementTree.fromstring(policy)

    assert root.find("./inbound")[0].tag == "cors"
    assert root.findtext("./inbound/cors/allowed-origins/origin") == (
        "https://prototype.example.com"
    )
    token_validation = root.find("./inbound/choose/otherwise/validate-azure-ad-token")
    assert token_validation is not None
    assert token_validation.attrib["tenant-id"] == "prototype-tenant"
    assert [item.text for item in token_validation.findall("./audiences/audience")] == [
        "prototype-client",
        "api://prototype-client",
    ]
    rate_limit = root.find("./inbound/rate-limit-by-key")
    assert rate_limit is not None
    assert rate_limit.attrib["calls"] == "120"
    assert root.find("./inbound/forward-request") is None
    forward_request = root.find("./backend/forward-request")
    assert forward_request is not None
    assert forward_request.attrib["timeout"] == "300"
    assert "mise" not in policy.lower()


async def test_provision_infrastructure_creates_isolated_private_runtime(monkeypatch):
    service = PrototypeApiGatewayService(
        subscription_id="sub-123",
        location="eastus2",
        publisher_email="genie@example.com",
        publisher_name="Genie",
    )
    captured = {}
    network_client = SimpleNamespace(
        network_security_groups=SimpleNamespace(
            begin_create_or_update=lambda resource_group, name, model: captured.update(
                network_security_group=model,
            )
            or _poller(SimpleNamespace())
        ),
        virtual_networks=SimpleNamespace(
            begin_create_or_update=lambda resource_group, name, model: captured.update(
                resource_group=resource_group,
                vnet_name=name,
                vnet=model,
            )
            or _poller(SimpleNamespace())
        )
    )
    environment = SimpleNamespace(
        id="/subscriptions/sub-123/resourceGroups/genie-proto-claims-1234/providers/"
        "Microsoft.App/managedEnvironments/private-env",
        default_domain="private.internal.example.com",
        static_ip="10.0.1.4",
    )
    container_apps_client = SimpleNamespace(
        managed_environments=SimpleNamespace(
            begin_create_or_update=lambda resource_group, name, model: captured.update(
                environment_name=name,
                environment=model,
            )
            or _poller(environment)
        )
    )
    dns_client = SimpleNamespace(
        private_zones=SimpleNamespace(
            begin_create_or_update=lambda *args: captured.update(zone=args)
            or _poller(SimpleNamespace())
        ),
        record_sets=SimpleNamespace(
            create_or_update=lambda *args: captured.update(record=args)
        ),
        virtual_network_links=SimpleNamespace(
            begin_create_or_update=lambda *args: captured.update(link=args)
            or _poller(SimpleNamespace())
        ),
    )
    apim_client = SimpleNamespace(
        api_management_service=SimpleNamespace(
            begin_create_or_update=lambda resource_group, name, model: captured.update(
                apim_name=name,
                apim=model,
            )
            or _poller(SimpleNamespace())
        )
    )
    monkeypatch.setattr(service, "_network_client", lambda: network_client)
    monkeypatch.setattr(service, "_container_apps_client", lambda: container_apps_client)
    monkeypatch.setattr(service, "_private_dns_client", lambda: dns_client)
    monkeypatch.setattr(service, "_api_management_client", lambda: apim_client)

    result = await service.provision_infrastructure(mission_slug="claims-1234")

    assert captured["resource_group"] == "genie-proto-claims-1234"
    subnet_delegations = {
        subnet.name: subnet.delegations[0].service_name
        for subnet in captured["vnet"].subnets
    }
    assert subnet_delegations == {
        "container-apps": "Microsoft.App/environments",
        "api-management": "Microsoft.Web/serverFarms",
    }
    gateway_subnet = next(
        subnet for subnet in captured["vnet"].subnets if subnet.name == "api-management"
    )
    assert gateway_subnet.network_security_group.id.startswith(
        "/subscriptions/sub-123/resourceGroups/genie-proto-claims-1234/providers/"
        "Microsoft.Network/networkSecurityGroups/genie-apim-"
    )
    assert captured["environment"].vnet_configuration.internal is True
    assert result.managed_environment_id == environment.id
    assert captured["zone"][1] == environment.default_domain
    assert captured["record"][2:4] == ("A", "*")
    assert captured["apim"].sku.name == "StandardV2"
    assert captured["apim"].configuration_api.legacy_api == "Disabled"
    assert captured["apim"].developer_portal_status == "Disabled"
    assert captured["apim"].legacy_portal_status == "Disabled"
    assert captured["apim"].virtual_network_type == "External"
    assert captured["apim"].virtual_network_configuration.subnet_resource_id.endswith(
        "/subnets/api-management"
    )
    assert result.api_management_service_name == captured["apim_name"]


async def test_publish_api_routes_supported_methods_to_private_backend(monkeypatch):
    service = PrototypeApiGatewayService(
        subscription_id="sub-123",
        location="eastus2",
        publisher_email="genie@example.com",
        publisher_name="Genie",
    )
    captured = {"operations": []}
    api_client = SimpleNamespace(
        begin_create_or_update=lambda *args: captured.update(api=args)
        or _poller(SimpleNamespace())
    )
    operation_client = SimpleNamespace(
        create_or_update=lambda *args: captured["operations"].append(args)
    )
    policy_client = SimpleNamespace(
        create_or_update=lambda *args: captured.update(policy=args)
    )
    named_value_client = SimpleNamespace(
        create_or_update=lambda *args: captured.update(named_value=args)
    )
    service_client = SimpleNamespace(
        get=lambda *_: SimpleNamespace(gateway_url="https://claims.azure-api.net/")
    )
    monkeypatch.setattr(
        service,
        "_api_management_client",
        lambda: SimpleNamespace(
            api=api_client,
            api_operation=operation_client,
            api_policy=policy_client,
            named_value=named_value_client,
            api_management_service=service_client,
        ),
    )

    gateway_url = await service.publish_api(
        mission_slug="claims-1234",
        backend_url="https://claims.private.internal",
        acceptance_test_key="acceptance-secret",
        authentication=_authentication(),
    )

    assert gateway_url == "https://claims.azure-api.net"
    api_model = captured["api"][3]
    assert api_model.service_url == "https://claims.private.internal"
    assert api_model.subscription_required is False
    assert {args[4].method for args in captured["operations"]} == {
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "HEAD",
    }
    assert all(args[4].url_template == "/*" for args in captured["operations"])
    named_value = captured["named_value"][3]
    assert named_value.secret is True
    assert named_value.value == "acceptance-secret"
    policy = captured["policy"][4].value
    assert "validate-azure-ad-token" in policy
    assert "{{acceptance-test-key}}" in policy
    assert "https://prototype.invalid" in policy