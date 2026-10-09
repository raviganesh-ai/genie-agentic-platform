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
        shared_vnet_resource_id: str | None = None,
        shared_network_resource_group: str | None = None,
        shared_private_dns_zone_names: tuple[str, ...] = (),
        shared_entra_tenant_id: str | None = None,
        shared_entra_client_id: str | None = None,
        shared_entra_audience: str | None = None,
        shared_entra_api_scope: str = "prototype.access",
    ) -> None:
        self._subscription_id = subscription_id
        self._location = location
        self._publisher_email = publisher_email
        self._publisher_name = publisher_name
        self._sku_name = sku_name
        self._capacity = capacity
        self._shared_vnet_resource_id = shared_vnet_resource_id
        self._shared_network_resource_group = shared_network_resource_group
        self._shared_private_dns_zone_names = shared_private_dns_zone_names
        self._shared_entra_tenant_id = shared_entra_tenant_id
        self._shared_entra_client_id = shared_entra_client_id
        self._shared_entra_audience = shared_entra_audience
        self._shared_entra_api_scope = shared_entra_api_scope

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

    async def ensure_spa_redirect_uri(self, frontend_url: str) -> None:
        """Adds ``frontend_url`` to Genie's one shared Entra ID App
        Registration's SPA redirect URI list, additively - never replacing
        another mission's own redirect URI, since many missions share this
        one app (see ``shared_entra_client_id`` on ``Settings``).

        A no-op when the shared app isn't configured, matching every other
        optional-collaborator pattern in this package. Requires Genie's own
        deployment identity to hold Microsoft Graph ``Application.ReadWrite.
        OwnedBy`` (scoped to just this one, Genie-owned app) - a narrower
        grant than tenant-wide application management, because Genie
        deliberately never creates or owns a new app registration per
        mission.
        """

        if not self._shared_entra_client_id:
            return

        base_url = (
            "https://graph.microsoft.com/v1.0/applications(appId="
            f"'{self._shared_entra_client_id}')"
        )
        async with self._graph_credential() as credential:
            token = await credential.get_token("https://graph.microsoft.com/.default")
            headers = {
                "Authorization": f"Bearer {token.token}",
                "Content-Type": "application/json",
            }
            async with self._graph_http_client() as client:
                response = await client.get(f"{base_url}?$select=spa", headers=headers)
                response.raise_for_status()
                current_uris = set((response.json().get("spa") or {}).get("redirectUris") or [])
                if frontend_url in current_uris:
                    return
                current_uris.add(frontend_url)
                response = await client.patch(
                    base_url,
                    headers=headers,
                    json={"spa": {"redirectUris": sorted(current_uris)}},
                )
                response.raise_for_status()

    def _graph_credential(self) -> Any:
        """Separated for dependency injection in tests - the real
        implementation is just ``azure.identity.aio.DefaultAzureCredential``,
        never mocked/faked in production code."""
        from azure.identity.aio import DefaultAzureCredential

        return DefaultAzureCredential()

    def _graph_http_client(self) -> Any:
        """Separated for dependency injection in tests - the real
        implementation is just ``httpx.AsyncClient``, never mocked/faked
        in production code."""
        import httpx

        return httpx.AsyncClient(timeout=30)

    async def _ensure_identity_named_values(
        self, *, api_management_client: Any, resource_group: str, service_name: str
    ) -> GatewayPolicyConfig | None:
        """Creates/updates the APIM Named Values ``validate-azure-ad-token``
        reads its tenant id/audience from, and returns the
        ``GatewayPolicyConfig`` referencing them - or ``None`` when the
        shared Entra app isn't configured, so a mission that doesn't need
        sign-in enforcement never gets one (a no-op, not a failure)."""

        if not (self._shared_entra_tenant_id and self._shared_entra_audience):
            return None

        from azure.mgmt.apimanagement.models import NamedValueCreateContract

        tenant_id_named_value = "genie-shared-entra-tenant-id"
        audience_named_value = "genie-shared-entra-audience"
        for name, value in (
            (tenant_id_named_value, self._shared_entra_tenant_id),
            (audience_named_value, self._shared_entra_audience),
        ):
            poller = api_management_client.named_value.begin_create_or_update(
                resource_group,
                service_name,
                name,
                NamedValueCreateContract(display_name=name, value=value, secret=False),
            )
            await asyncio.to_thread(poller.result)
        return GatewayPolicyConfig(
            tenant_id_named_value=tenant_id_named_value,
            audience_named_value=audience_named_value,
            required_claim_values=(),
        )

    async def _wait_for_private_dns_zone_group(
        self,
        *,
        network_client: Any,
        resource_group: str,
        private_endpoint_name: str,
        zone_group_name: str,
    ) -> None:
        deadline = asyncio.get_running_loop().time() + 600
        last_state = "not found"
        while asyncio.get_running_loop().time() < deadline:
            try:
                zone_group = await asyncio.to_thread(
                    network_client.private_dns_zone_groups.get,
                    resource_group,
                    private_endpoint_name,
                    zone_group_name,
                )
                last_state = getattr(zone_group, "provisioning_state", None) or "unknown"
                if last_state == "Succeeded":
                    return
                if last_state in {"Failed", "Canceled"}:
                    raise PrototypeApiGatewayError(
                        "Cosmos DB private DNS zone group provisioning ended with "
                        f"state '{last_state}'."
                    )
            except PrototypeApiGatewayError:
                raise
            except Exception as exc:
                last_state = str(exc)
            await asyncio.sleep(5)
        raise PrototypeApiGatewayError(
            "Cosmos DB private DNS zone group did not become ready within "
            f"600 seconds (last state: {last_state})."
        )

    async def _peer_with_shared_network(
        self,
        *,
        network_client: Any,
        resource_group: str,
        vnet_name: str,
        vnet_id: str,
        report: GatewayProgressCallback,
    ) -> None:
        """Peers this mission's own isolated VNet with Genie's shared VNet,
        and links the mission's VNet to every shared private DNS zone named
        in settings - the real, observed fix for missions whose generated
        backend/agents could not reach Genie's own shared, private-endpoint-
        only resources (the shared Container Registry, the shared Azure AI
        Foundry account) at all, because the mission's own VNet had no
        private network path to them.

        This is deliberately the ONLY supported fix for that class of
        failure - it must never be "solved" by disabling public network
        access restrictions on the shared resource itself (every shared
        resource stays exactly as private as it was before this mission
        existed); only this mission's own VNet gains a private path in.

        A no-op (with a clear progress message) when
        ``shared_vnet_resource_id`` is not configured - matching
        every other optional-collaborator pattern in this package - so a
        deployment that hasn't configured shared-network peering yet does
        not fail closed on a feature it never opted into, but also never
        gets silent, broken connectivity.
        """

        if not self._shared_vnet_resource_id or not self._shared_network_resource_group:
            await report(
                "Shared-network peering is not configured "
                "(shared_vnet_resource_id); skipping."
            )
            return

        await report("Peering the prototype network with Genie's shared network...")

        from azure.mgmt.network.models import SubResource, VirtualNetworkPeering

        shared_vnet_parts = self._shared_vnet_resource_id.rstrip("/").split("/")
        shared_vnet_name = shared_vnet_parts[-1]

        # Peering is two one-directional resources, one declared on each
        # VNet, both required before Azure reports either side "Connected".
        forward_poller = network_client.virtual_network_peerings.begin_create_or_update(
            resource_group,
            vnet_name,
            f"peer-to-{shared_vnet_name}",
            VirtualNetworkPeering(
                remote_virtual_network=SubResource(id=self._shared_vnet_resource_id),
                allow_virtual_network_access=True,
                allow_forwarded_traffic=False,
                allow_gateway_transit=False,
                use_remote_gateways=False,
            ),
        )
        await asyncio.to_thread(forward_poller.result)

        reverse_poller = network_client.virtual_network_peerings.begin_create_or_update(
            self._shared_network_resource_group,
            shared_vnet_name,
            f"peer-to-{vnet_name}",
            VirtualNetworkPeering(
                remote_virtual_network=SubResource(id=vnet_id),
                allow_virtual_network_access=True,
                allow_forwarded_traffic=False,
                allow_gateway_transit=False,
                use_remote_gateways=False,
            ),
        )
        await asyncio.to_thread(reverse_poller.result)

        # Peering alone only provides IP-level reachability - Azure Private
        # DNS Zones additionally require every resolving VNet to carry its
        # own explicit link to the zone, so the mission's own VNet resolves
        # each shared resource's private endpoint FQDN to its private IP.
        dns_client = self._private_dns_client()
        from azure.mgmt.privatedns.models import SubResource as PrivateDnsSubResource
        from azure.mgmt.privatedns.models import VirtualNetworkLink

        for zone_name in self._shared_private_dns_zone_names:
            link_poller = dns_client.virtual_network_links.begin_create_or_update(
                self._shared_network_resource_group,
                zone_name,
                _resource_name(
                    "link",
                    vnet_name,
                    maximum_length=80,
                    uniqueness_seed=f"{vnet_name}:{zone_name}",
                ),
                VirtualNetworkLink(
                    location="global",
                    virtual_network=PrivateDnsSubResource(id=vnet_id),
                    registration_enabled=False,
                ),
            )
            await asyncio.to_thread(link_poller.result)

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

            await self._peer_with_shared_network(
                network_client=network_client,
                resource_group=resource_group,
                vnet_name=vnet_name,
                vnet_id=vnet_id,
                report=_report,
            )

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
                await self._wait_for_private_dns_zone_group(
                    network_client=network_client,
                    resource_group=resource_group,
                    private_endpoint_name=private_endpoint_name,
                    zone_group_name="default",
                )

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
        require_sign_in: bool = False,
        on_progress: GatewayProgressCallback | None = None,
    ) -> str:
        if not backend_url.startswith("https://"):
            raise PrototypeApiGatewayError("Private prototype backend URL must use HTTPS.")
        if not frontend_origin.startswith("https://"):
            raise PrototypeApiGatewayError("Prototype gateway CORS origin must use HTTPS.")
        if require_sign_in and not (self._shared_entra_tenant_id and self._shared_entra_audience):
            raise PrototypeApiGatewayError(
                "This mission's architecture requires sign-in enforcement, but "
                "Genie's shared Entra ID app (shared_entra_tenant_id/"
                "shared_entra_audience) is not configured. Refusing to publish "
                "an unprotected API for a mission that declared it needs one - "
                "configure the shared app or remove the identity requirement."
            )
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
            gateway_policy = None
            if require_sign_in:
                # Generic for every mission whose requirements named an
                # identity provider (see architecture-coverage enforcement,
                # not an ACI-specific branch) - enforced here regardless of
                # what domain the mission is in.
                gateway_policy = await self._ensure_identity_named_values(
                    api_management_client=client,
                    resource_group=resource_group,
                    service_name=service_name,
                )
                if gateway_policy is None:
                    raise PrototypeApiGatewayError(
                        "This mission's architecture requires sign-in enforcement, but "
                        "Genie's shared Entra ID app (shared_entra_tenant_id/"
                        "shared_entra_audience) is not configured. Refusing to publish "
                        "an unprotected API for a mission that declared it needs one - "
                        "configure the shared app or remove the identity requirement."
                    )
            client.api_policy.create_or_update(
                resource_group,
                service_name,
                api_id,
                "policy",
                PolicyContract(
                    format="rawxml",
                    value=_build_api_policy(
                        frontend_origin=frontend_origin, gateway_policy=gateway_policy
                    ),
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
