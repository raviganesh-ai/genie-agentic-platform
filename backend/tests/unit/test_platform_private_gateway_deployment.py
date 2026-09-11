"""Deployment contracts for Genie's public APIM edge and private backend."""
from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def test_gateway_template_uses_standard_v2_vnet_integration_and_private_link():
    template = (_repo_root() / "infra" / "platform-private-gateway.bicep").read_text(
        encoding="utf-8"
    )

    assert "name: 'StandardV2'" in template
    assert "publicNetworkAccess: 'Enabled'" in template
    assert "virtualNetworkType: 'External'" in template
    assert "serviceName: 'Microsoft.Web/serverFarms'" in template
    assert "subscriptionRequired: false" in template
    assert "groupIds: [\n            'managedEnvironments'" in template
    assert "privateDnsZoneName = 'privatelink.${location}.azurecontainerapps.io'" in template


def test_foundational_network_reserves_the_apim_subnet():
    network = (_repo_root() / "infra" / "modules" / "virtual-network.bicep").read_text(
        encoding="utf-8"
    )

    assert "name: 'api-management'" in network
    assert "addressPrefix: apiManagementSubnetPrefix" in network
    assert "serviceName: 'Microsoft.Web/serverFarms'" in network
    assert "networkSecurityGroup:" in network


def test_gateway_policy_is_exact_origin_anonymous_edge_policy():
    policy_path = _repo_root() / "infra" / "policies" / "genie-api-policy.xml"
    root = ElementTree.parse(policy_path).getroot()

    assert root.findtext("./inbound/cors/allowed-origins/origin") == "__ALLOWED_ORIGIN__"
    assert root.find("./inbound/rate-limit-by-key") is not None
    assert root.find("./inbound/set-header[@name='X-Correlation-Id']") is not None
    assert root.find("./backend/forward-request") is not None


def test_cutover_proves_gateway_before_and_after_disabling_public_access():
    script = (_repo_root() / "scripts" / "deploy_platform_gateway.ps1").read_text(
        encoding="utf-8"
    )

    gateway_probe = "Wait-ForGatewayReadiness -GatewayUrl $gatewayUrl -Deadline $deadline"
    first_probe = script.index(gateway_probe)
    private_dns_deploy = script.index("Preparing private DNS for")
    disable_public_access = script.index('publicNetworkAccess = "Disabled"')
    private_endpoint_deploy = script.index("Creating the Container Apps private endpoint")
    second_probe = script.rindex(gateway_probe)

    assert (
        first_probe
        < private_dns_deploy
        < disable_public_access
        < private_endpoint_deploy
        < second_probe
    )
    assert "if ($PrepareOnly)" in script
    assert 'cutover = "pending"' in script
    assert "Wait-ForPrivateEndpointApproval" in script
    assert 'Where-Object { $_ -ne "Approved" }' in script
    assert '$_ -in @("Rejected", "Disconnected")' in script
    assert "Waiting for Container Apps private endpoint approval" in script
    assert 'throw "Direct Container Apps ingress still accepts public requests."' in script
    assert "--method patch" in script
    assert '"$($environment.id)?api-version=2025-10-02-preview"' in script
    assert '"$($environment.id)?api-version=2025-01-01"' not in script
    assert "--public-network-access" not in script
    assert 'if ($environment.properties.publicNetworkAccess -eq "Disabled")' in script
    assert "Resuming the fail-closed private endpoint cutover" in script
    assert '$deploymentError -notmatch "ManagedEnvironmentNotHealthy"' in script
    assert '$existingEndpoint.provisioningState -eq "Succeeded"' in script
    assert "$privateEndpoint.privateLinkServiceConnections" in script
    assert "$_.privateLinkServiceConnectionState.status" in script
    assert "$existingEndpoint.properties.provisioningState" not in script
    assert "$privateEndpoint.properties.privateLinkServiceConnections" not in script
    assert "Private endpoint already exists; preserving its connection state" in script
    assert "Start-Sleep -Seconds 30" in script


def test_ci_passes_the_verified_gateway_to_backend_and_frontend():
    workflow = (_repo_root() / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert "gateway-url: ${{ steps.prepare.outputs.gateway-url }}" in workflow
    assert "needs: [prepare-gateway]" in workflow
    assert "needs: [backend, frontend, prepare-gateway, deploy-frontend]" in workflow
    assert '-GatewayUrl "${{ needs.prepare-gateway.outputs.gateway-url }}"' in workflow
    assert "VITE_GENIE_API_BASE_URL: ${{ needs.prepare-gateway.outputs.gateway-url }}" in workflow
    assert "vars.VITE_GENIE_API_BASE_URL" not in workflow


def test_gateway_deployer_role_is_resource_group_scoped_and_has_no_delete_actions():
    script = (
        _repo_root() / "scripts" / "configure_platform_gateway_deployer.ps1"
    ).read_text(encoding="utf-8")

    assert '$resourceGroupScope = "$subscriptionScope/resourceGroups/$ResourceGroup"' in script
    assert "Microsoft.Resources/deployments/write" in script
    assert "Microsoft.ApiManagement/service/apis/policies/write" in script
    assert "Microsoft.Network/virtualNetworks/join/action" in script
    assert "Microsoft.Network/privateEndpoints/write" in script
    assert "Microsoft.App/managedEnvironments/write" in script
    assert "& az rest" in script
    assert "api-version=2022-04-01" in script
    actions = script.split("Actions = @(", maxsplit=1)[1].split("    )", maxsplit=1)[0]
    assert "Microsoft.Authorization/" not in actions
    assert '/delete"' not in actions
