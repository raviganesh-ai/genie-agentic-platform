"""Dedicated API gateway and private runtime infrastructure for prototypes."""
from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree

from app.deploy_launch.resource_naming import prototype_resource_group_name

GatewayProgressCallback = Callable[[str], Awaitable[None]]

_PROXY_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD")


class PrototypeApiGatewayError(RuntimeError):
    """Raised when dedicated prototype gateway infrastructure cannot be provisioned."""


@dataclass(frozen=True)
class PrototypeGatewayInfrastructure:
    managed_environment_id: str
    api_management_service_name: str


def _resource_name(
    prefix: str,
    mission_slug: str,
    *,
    maximum_length: int,
    uniqueness_seed: str | None = None,
) -> str:
    normalized = re.sub(r"[^a-z0-9-]", "-", mission_slug.lower()).strip("-") or "prototype"
    digest = hashlib.sha256((uniqueness_seed or mission_slug).encode("utf-8")).hexdigest()[:10]
    stem_length = maximum_length - len(prefix) - len(digest) - 2
    return f"{prefix}-{normalized[:stem_length].rstrip('-')}-{digest}"


@dataclass(frozen=True)
class GatewayPolicyPathRule:
    """One path-prefix-scoped access rule within a mission's gateway
    policy (see "## Gateway Policies" in architecture_parsing.py) -
    policy-based denial for a specific product/operation path, distinct
    from the mission's own default/global requirement."""

    path_prefix: str
    required_claim_values: tuple[str, ...]


@dataclass(frozen=True)
class GatewayPolicyConfig:
    """Parsed, deterministic input to ``_build_api_policy``'s JWT
    validation/entitlement rendering - never raw LLM-authored APIM policy
    XML (see build-generation-component-v1's "gateway_policy" branch,
    which instead has the Build Agent emit a small, structured YAML
    configuration that this dataclass's own loader parses). Rendering the
    actual policy XML deterministically in Python, from this narrow,
    validated shape, keeps the security-sensitive part of the pipeline
    (what APIM policy XML is actually applied) out of free-form LLM
    output.

    ``tenant_id_named_value``/``audience_named_value`` name APIM Named
    Values (never a literal tenant id/audience string) - the real value
    is configured against the provisioned APIM instance itself (backed by
    Key Vault where appropriate), matching Genie's own no-hardcoding rule.
    """

    tenant_id_named_value: str
    audience_named_value: str
    required_claim_name: str = "roles"
    required_claim_values: tuple[str, ...] = ()
    path_rules: tuple[GatewayPolicyPathRule, ...] = ()


def _add_validate_azure_ad_token(
    parent: ElementTree.Element,
    *,
    tenant_id_named_value: str,
    audience_named_value: str,
    required_claim_name: str,
    required_claim_values: tuple[str, ...],
) -> None:
    validate = ElementTree.SubElement(
        parent,
        "validate-azure-ad-token",
        {
            "tenant-id": f"{{{{{tenant_id_named_value}}}}}",
            "header-name": "Authorization",
            "failed-validation-httpcode": "401",
            "failed-validation-error-message": "Unauthorized. Access token is missing or invalid.",
        },
    )
    audiences = ElementTree.SubElement(validate, "audiences")
    ElementTree.SubElement(audiences, "audience").text = f"{{{{{audience_named_value}}}}}"
    if required_claim_values:
        required_claims = ElementTree.SubElement(validate, "required-claims")
        claim = ElementTree.SubElement(required_claims, "claim", {"name": required_claim_name, "match": "any"})
        for value in required_claim_values:
            ElementTree.SubElement(claim, "value").text = value


def _build_api_policy(
    *, frontend_origin: str, gateway_policy: GatewayPolicyConfig | None = None
) -> str:
    policies = ElementTree.Element("policies")
    inbound = ElementTree.SubElement(policies, "inbound")
    cors = ElementTree.SubElement(
        inbound,
        "cors",
        {"allow-credentials": "false", "terminate-unmatched-request": "true"},
    )
    allowed_origins = ElementTree.SubElement(cors, "allowed-origins")
    ElementTree.SubElement(allowed_origins, "origin").text = frontend_origin.rstrip("/")
    allowed_methods = ElementTree.SubElement(cors, "allowed-methods")
    ElementTree.SubElement(allowed_methods, "method").text = "*"
    allowed_headers = ElementTree.SubElement(cors, "allowed-headers")
    ElementTree.SubElement(allowed_headers, "header").text = "*"
    exposed_headers = ElementTree.SubElement(cors, "expose-headers")
    ElementTree.SubElement(exposed_headers, "header").text = "X-Correlation-Id"
    ElementTree.SubElement(inbound, "base")
    ElementTree.SubElement(
        inbound,
        "rate-limit-by-key",
        {
            "calls": "120",
            "renewal-period": "60",
            "counter-key": "@(context.Request.IpAddress)",
        },
    )
    correlation_header = ElementTree.SubElement(
        inbound,
        "set-header",
        {"name": "X-Correlation-Id", "exists-action": "skip"},
    )
    ElementTree.SubElement(correlation_header, "value").text = "@(context.RequestId.ToString())"
    if gateway_policy is not None:
        if gateway_policy.path_rules:
            # Policy-based denial per product/operation path (FR-008) -
            # each declared path rule gets its own stricter claim
            # requirement; any path not matched falls through to the
            # mission's own default/global requirement.
            choose = ElementTree.SubElement(inbound, "choose")
            for rule in gateway_policy.path_rules:
                when = ElementTree.SubElement(
                    choose,
                    "when",
                    {
                        "condition": (
                            '@(context.Request.OriginalUrl.Path.StartsWith('
                            f'"{rule.path_prefix}", StringComparison.OrdinalIgnoreCase))'
                        )
                    },
                )
                _add_validate_azure_ad_token(
                    when,
                    tenant_id_named_value=gateway_policy.tenant_id_named_value,
                    audience_named_value=gateway_policy.audience_named_value,
                    required_claim_name=gateway_policy.required_claim_name,
                    required_claim_values=rule.required_claim_values,
                )
            otherwise = ElementTree.SubElement(choose, "otherwise")
            _add_validate_azure_ad_token(
                otherwise,
                tenant_id_named_value=gateway_policy.tenant_id_named_value,
                audience_named_value=gateway_policy.audience_named_value,
                required_claim_name=gateway_policy.required_claim_name,
                required_claim_values=gateway_policy.required_claim_values,
            )
        else:
            _add_validate_azure_ad_token(
                inbound,
                tenant_id_named_value=gateway_policy.tenant_id_named_value,
                audience_named_value=gateway_policy.audience_named_value,
                required_claim_name=gateway_policy.required_claim_name,
                required_claim_values=gateway_policy.required_claim_values,
            )
    backend = ElementTree.SubElement(policies, "backend")
    ElementTree.SubElement(backend, "forward-request", {"timeout": "300"})
    for section_name in ("outbound", "on-error"):
        section = ElementTree.SubElement(policies, section_name)
        ElementTree.SubElement(section, "base")
    return ElementTree.tostring(policies, encoding="unicode")


class PrototypeApiGatewayService:
    """Owns each prototype's VNet, internal app environment, DNS, and APIM service."""

    def __init__(
        self,
        *,
        subscription_id: str,
        location: str,
        publisher_email: str,
        publisher_name: str,
        sku_name: str = "StandardV2",
        capacity: int = 1,
    ) -> None:
        self._subscription_id = subscription_id
        self._location = location
        self._publisher_email = publisher_email
        self._publisher_name = publisher_name
        self._sku_name = sku_name
        self._capacity = capacity

    def _credential(self) -> Any:
        try:
            from azure.identity import DefaultAzureCredential
        except ImportError as exc:
            raise PrototypeApiGatewayError("azure-identity is not installed.") from exc
        return DefaultAzureCredential()

    def _network_client(self) -> Any:
        try:
            from azure.mgmt.network import NetworkManagementClient
        except ImportError as exc:
            raise PrototypeApiGatewayError("azure-mgmt-network is not installed.") from exc
        return NetworkManagementClient(self._credential(), self._subscription_id)

    def _container_apps_client(self) -> Any:
        try:
            from azure.mgmt.appcontainers import ContainerAppsAPIClient
        except ImportError as exc:
            raise PrototypeApiGatewayError("azure-mgmt-appcontainers is not installed.") from exc
        return ContainerAppsAPIClient(self._credential(), self._subscription_id)

    def _private_dns_client(self) -> Any:
        try:
            from azure.mgmt.privatedns import PrivateDnsManagementClient
        except ImportError as exc:
            raise PrototypeApiGatewayError("azure-mgmt-privatedns is not installed.") from exc
        return PrivateDnsManagementClient(self._credential(), self._subscription_id)

    def _api_management_client(self) -> Any:
        try:
            from azure.mgmt.apimanagement import ApiManagementClient
        except ImportError as exc:
            raise PrototypeApiGatewayError("azure-mgmt-apimanagement is not installed.") from exc
        return ApiManagementClient(self._credential(), self._subscription_id)

    async def provision_infrastructure(
        self,
        *,
        mission_slug: str,
        data_endpoint: str | None = None,
        on_progress: GatewayProgressCallback | None = None,
    ) -> PrototypeGatewayInfrastructure:
        async def _report(message: str) -> None:
            if on_progress is not None:
                await on_progress(message)

        resource_group = prototype_resource_group_name(mission_slug)
        vnet_name = _resource_name("genie", mission_slug, maximum_length=64)
        environment_name = _resource_name("genie", mission_slug, maximum_length=32)
        service_name = _resource_name(
            "genie",
            mission_slug,
            maximum_length=50,
            uniqueness_seed=f"{self._subscription_id}:{mission_slug}",
        )
        network_security_group_name = _resource_name("genie-apim", mission_slug, maximum_length=80)
        tags = {"genie-managed-by": "genie", "genie-mission-id": mission_slug}
        vnet_id = (
            f"/subscriptions/{self._subscription_id}/resourceGroups/{resource_group}/providers/"
            f"Microsoft.Network/virtualNetworks/{vnet_name}"
        )
        app_subnet_id = f"{vnet_id}/subnets/container-apps"
        private_endpoint_subnet_id = f"{vnet_id}/subnets/private-endpoints"
        gateway_subnet_id = f"{vnet_id}/subnets/api-management"
        network_security_group_id = (
            f"/subscriptions/{self._subscription_id}/resourceGroups/{resource_group}/providers/"
            f"Microsoft.Network/networkSecurityGroups/{network_security_group_name}"
        )

        try:
            from azure.mgmt.apimanagement.models import (
                ApiManagementServiceResource,
                ApiManagementServiceSkuProperties,
                ConfigurationApi,
                VirtualNetworkConfiguration,
            )
            from azure.mgmt.appcontainers.models import ManagedEnvironment, VnetConfiguration
            from azure.mgmt.network.models import (
                AddressSpace,
                Delegation,
                NetworkSecurityGroup,
                PrivateDnsZoneConfig,
                PrivateDnsZoneGroup,
                PrivateEndpoint,
                PrivateLinkServiceConnection,
                Subnet,
                VirtualNetwork,
            )
            from azure.mgmt.privatedns.models import (
                ARecord,
                PrivateZone,
                RecordSet,
                SubResource,
                VirtualNetworkLink,
            )

            await _report("Provisioning the prototype private network...")
            network_client = self._network_client()
            network_security_group_poller = (
                network_client.network_security_groups.begin_create_or_update(
                    resource_group,
                    network_security_group_name,
                    NetworkSecurityGroup(location=self._location, tags=tags),
                )
            )
            await asyncio.to_thread(network_security_group_poller.result)
            network_poller = network_client.virtual_networks.begin_create_or_update(
                resource_group,
                vnet_name,
                VirtualNetwork(
                    location=self._location,
                    tags=tags,
                    address_space=AddressSpace(address_prefixes=["10.0.0.0/16"]),
                    subnets=[
                        Subnet(
                            name="container-apps",
                            address_prefix="10.0.0.0/23",
                            delegations=[
                                Delegation(
                                    name="container-apps-delegation",
                                    service_name="Microsoft.App/environments",
                                )
                            ],
                        ),
                        Subnet(
                            name="private-endpoints",
                            address_prefix="10.0.2.0/24",
                            private_endpoint_network_policies="Disabled",
                        ),
                        Subnet(
                            name="api-management",
                            address_prefix="10.0.4.0/27",
                            network_security_group=NetworkSecurityGroup(
                                id=network_security_group_id
                            ),
                            delegations=[
                                Delegation(
                                    name="api-management-delegation",
                                    service_name="Microsoft.Web/serverFarms",
                                )
                            ],
                        ),
                    ],
                ),
            )
            await asyncio.to_thread(network_poller.result)

            dns_client = self._private_dns_client()
            if data_endpoint:
                account_name = (urlparse(data_endpoint).hostname or "").split(".", maxsplit=1)[0]
                if not account_name:
                    raise PrototypeApiGatewayError(
                        "Mission data endpoint does not contain a Cosmos DB account name."
                    )
                cosmos_account_id = (
                    f"/subscriptions/{self._subscription_id}/resourceGroups/{resource_group}/"
                    f"providers/Microsoft.DocumentDB/databaseAccounts/{account_name}"
                )
                cosmos_zone_name = "privatelink.documents.azure.com"
                cosmos_zone_id = (
                    f"/subscriptions/{self._subscription_id}/resourceGroups/{resource_group}/"
                    f"providers/Microsoft.Network/privateDnsZones/{cosmos_zone_name}"
                )
                private_endpoint_name = _resource_name(
                    "genie-cosmos-pe", mission_slug, maximum_length=80
                )

                await _report("Provisioning private Cosmos DB connectivity...")
                cosmos_zone_poller = dns_client.private_zones.begin_create_or_update(
                    resource_group,
                    cosmos_zone_name,
                    PrivateZone(location="global", tags=tags),
                )
                await asyncio.to_thread(cosmos_zone_poller.result)
                cosmos_link_poller = dns_client.virtual_network_links.begin_create_or_update(
                    resource_group,
                    cosmos_zone_name,
                    "prototype-cosmos-vnet",
                    VirtualNetworkLink(
                        location="global",
                        virtual_network=SubResource(id=vnet_id),
                        registration_enabled=False,
                        tags=tags,
                    ),
                )
                await asyncio.to_thread(cosmos_link_poller.result)
                private_endpoint_poller = network_client.private_endpoints.begin_create_or_update(
                    resource_group,
                    private_endpoint_name,
                    PrivateEndpoint(
                        location=self._location,
                        tags=tags,
                        subnet=Subnet(id=private_endpoint_subnet_id),
                        private_link_service_connections=[
                            PrivateLinkServiceConnection(
                                name="cosmos-sql",
                                private_link_service_id=cosmos_account_id,
                                group_ids=["Sql"],
                            )
                        ],
                    ),
                )
                await asyncio.to_thread(private_endpoint_poller.result)
                zone_group_poller = (
                    network_client.private_dns_zone_groups.begin_create_or_update(
                        resource_group,
                        private_endpoint_name,
                        "default",
                        PrivateDnsZoneGroup(
                            private_dns_zone_configs=[
                                PrivateDnsZoneConfig(
                                    name="cosmos",
                                    private_dns_zone_id=cosmos_zone_id,
                                )
                            ]
                        ),
                    )
                )
                await asyncio.to_thread(zone_group_poller.result)

            await _report("Provisioning the prototype internal Container Apps environment...")
            environment_poller = self._container_apps_client().managed_environments.begin_create_or_update(
                resource_group,
                environment_name,
                ManagedEnvironment(
                    location=self._location,
                    tags=tags,
                    vnet_configuration=VnetConfiguration(
                        internal=True,
                        infrastructure_subnet_id=app_subnet_id,
                    ),
                ),
            )
            environment = await asyncio.to_thread(environment_poller.result)
            if not environment.id or not environment.default_domain or not environment.static_ip:
                raise PrototypeApiGatewayError(
                    "Internal Container Apps environment returned incomplete network metadata."
                )

            await _report("Configuring private DNS for the prototype backend...")
            zone_poller = dns_client.private_zones.begin_create_or_update(
                resource_group,
                environment.default_domain,
                PrivateZone(location="global", tags=tags),
            )
            await asyncio.to_thread(zone_poller.result)
            dns_client.record_sets.create_or_update(
                resource_group,
                environment.default_domain,
                "A",
                "*",
                RecordSet(ttl=300, a_records=[ARecord(ipv4_address=environment.static_ip)]),
            )
            link_poller = dns_client.virtual_network_links.begin_create_or_update(
                resource_group,
                environment.default_domain,
                "prototype-vnet",
                VirtualNetworkLink(
                    location="global",
                    virtual_network=SubResource(id=vnet_id),
                    registration_enabled=False,
                    tags=tags,
                ),
            )
            await asyncio.to_thread(link_poller.result)

            await _report("Provisioning the prototype Azure API Management gateway...")
            gateway_poller = self._api_management_client().api_management_service.begin_create_or_update(
                resource_group,
                service_name,
                ApiManagementServiceResource(
                    location=self._location,
                    publisher_email=self._publisher_email,
                    publisher_name=self._publisher_name,
                    sku=ApiManagementServiceSkuProperties(
                        name=self._sku_name,
                        capacity=self._capacity,
                    ),
                    tags=tags,
                    public_network_access="Enabled",
                    configuration_api=ConfigurationApi(legacy_api="Disabled"),
                    developer_portal_status="Disabled",
                    legacy_portal_status="Disabled",
                    virtual_network_type="External",
                    virtual_network_configuration=VirtualNetworkConfiguration(
                        subnet_resource_id=gateway_subnet_id
                    ),
                ),
            )
            await asyncio.to_thread(gateway_poller.result)
            return PrototypeGatewayInfrastructure(
                managed_environment_id=environment.id,
                api_management_service_name=service_name,
            )
        except PrototypeApiGatewayError:
            raise
        except Exception as exc:
            raise PrototypeApiGatewayError(
                f"Failed to provision dedicated prototype gateway infrastructure: {exc}"
            ) from exc

    async def publish_api(
        self,
        *,
        mission_slug: str,
        backend_url: str,
        frontend_origin: str = "https://prototype.invalid",
        on_progress: GatewayProgressCallback | None = None,
    ) -> str:
        if not backend_url.startswith("https://"):
            raise PrototypeApiGatewayError("Private prototype backend URL must use HTTPS.")
        if not frontend_origin.startswith("https://"):
            raise PrototypeApiGatewayError("Prototype gateway CORS origin must use HTTPS.")
        if on_progress is not None:
            await on_progress("Publishing the prototype API through its gateway...")

        resource_group = prototype_resource_group_name(mission_slug)
        service_name = _resource_name(
            "genie",
            mission_slug,
            maximum_length=50,
            uniqueness_seed=f"{self._subscription_id}:{mission_slug}",
        )
        api_id = "prototype"
        try:
            from azure.mgmt.apimanagement.models import (
                ApiCreateOrUpdateParameter,
                OperationContract,
                PolicyContract,
            )

            client = self._api_management_client()
            api_poller = client.api.begin_create_or_update(
                resource_group,
                service_name,
                api_id,
                ApiCreateOrUpdateParameter(
                    display_name=mission_slug,
                    path="",
                    protocols=["https"],
                    service_url=backend_url.rstrip("/"),
                    subscription_required=False,
                ),
            )
            await asyncio.to_thread(api_poller.result)
            for method in _PROXY_METHODS:
                client.api_operation.create_or_update(
                    resource_group,
                    service_name,
                    api_id,
                    f"proxy-{method.lower()}",
                    OperationContract(
                        display_name=f"{method} proxy",
                        method=method,
                        url_template="/*",
                    ),
                )
            client.api_policy.create_or_update(
                resource_group,
                service_name,
                api_id,
                "policy",
                PolicyContract(
                    format="rawxml",
                    value=_build_api_policy(frontend_origin=frontend_origin),
                ),
            )
            service = client.api_management_service.get(resource_group, service_name)
            gateway_url = getattr(service, "gateway_url", None)
            if not gateway_url:
                raise PrototypeApiGatewayError(
                    "Azure API Management returned no public gateway URL."
                )
            return gateway_url.rstrip("/")
        except PrototypeApiGatewayError:
            raise
        except Exception as exc:
            raise PrototypeApiGatewayError(
                f"Failed to publish the prototype API through Azure API Management: {exc}"
            ) from exc

    async def configure_frontend_origin(
        self,
        *,
        mission_slug: str,
        frontend_origin: str,
    ) -> None:
        if not frontend_origin.startswith("https://"):
            raise PrototypeApiGatewayError("Prototype gateway CORS origin must use HTTPS.")
        resource_group = prototype_resource_group_name(mission_slug)
        service_name = _resource_name(
            "genie",
            mission_slug,
            maximum_length=50,
            uniqueness_seed=f"{self._subscription_id}:{mission_slug}",
        )
        try:
            from azure.mgmt.apimanagement.models import PolicyContract

            self._api_management_client().api_policy.create_or_update(
                resource_group,
                service_name,
                "prototype",
                "policy",
                PolicyContract(
                    format="rawxml",
                    value=_build_api_policy(frontend_origin=frontend_origin),
                ),
            )
        except Exception as exc:
            raise PrototypeApiGatewayError(
                f"Failed to configure prototype gateway CORS origin: {exc}"
            ) from exc