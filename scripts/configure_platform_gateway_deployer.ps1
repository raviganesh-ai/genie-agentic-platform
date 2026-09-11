<#
.SYNOPSIS
    Grants the GitHub Actions OIDC principal least-privilege rights for the
    Genie platform APIM and private-backend cutover.

.DESCRIPTION
    Creates or updates a custom role definition with only the ARM deployment,
    APIM API, VNet subnet, NSG, private endpoint, private DNS, and Container
    Apps environment actions required by deploy_platform_gateway.ps1. The role
    assignment is scoped to one Genie resource group.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SubscriptionId,

    [Parameter(Mandatory = $true)]
    [string]$ResourceGroup,

    [Parameter(Mandatory = $true)]
    [string]$PrincipalObjectId,

    [string]$RoleName = "Genie Platform Gateway Deployer"
)

$ErrorActionPreference = "Stop"

$subscriptionScope = "/subscriptions/$SubscriptionId"
$resourceGroupScope = "$subscriptionScope/resourceGroups/$ResourceGroup"
$roleDefinition = @{
    Name = $RoleName
    IsCustom = $true
    Description = "Deploys Genie's public APIM edge and private Container Apps network boundary."
    Actions = @(
        "Microsoft.Resources/subscriptions/resourceGroups/read"
        "Microsoft.Resources/deployments/read"
        "Microsoft.Resources/deployments/write"
        "Microsoft.Resources/deployments/validate/action"
        "Microsoft.Resources/deployments/whatIf/action"
        "Microsoft.Resources/deployments/operationStatuses/read"
        "Microsoft.Resources/deployments/operations/read"
        "Microsoft.ApiManagement/checkNameAvailability/read"
        "Microsoft.ApiManagement/service/read"
        "Microsoft.ApiManagement/service/write"
        "Microsoft.ApiManagement/service/apis/read"
        "Microsoft.ApiManagement/service/apis/write"
        "Microsoft.ApiManagement/service/apis/operations/read"
        "Microsoft.ApiManagement/service/apis/operations/write"
        "Microsoft.ApiManagement/service/apis/policies/read"
        "Microsoft.ApiManagement/service/apis/policies/write"
        "Microsoft.Network/virtualNetworks/read"
        "Microsoft.Network/virtualNetworks/join/action"
        "Microsoft.Network/virtualNetworks/subnets/read"
        "Microsoft.Network/virtualNetworks/subnets/write"
        "Microsoft.Network/virtualNetworks/subnets/join/action"
        "Microsoft.Network/networkSecurityGroups/read"
        "Microsoft.Network/networkSecurityGroups/write"
        "Microsoft.Network/networkSecurityGroups/join/action"
        "Microsoft.Network/privateEndpoints/read"
        "Microsoft.Network/privateEndpoints/write"
        "Microsoft.Network/privateEndpoints/privateDnsZoneGroups/read"
        "Microsoft.Network/privateEndpoints/privateDnsZoneGroups/write"
        "Microsoft.Network/privateDnsZones/read"
        "Microsoft.Network/privateDnsZones/write"
        "Microsoft.Network/privateDnsZones/join/action"
        "Microsoft.Network/privateDnsZones/virtualNetworkLinks/read"
        "Microsoft.Network/privateDnsZones/virtualNetworkLinks/write"
        "Microsoft.App/containerApps/read"
        "Microsoft.App/managedEnvironments/read"
        "Microsoft.App/managedEnvironments/write"
        "Microsoft.App/managedEnvironments/privateEndpointConnectionsApproval/action"
    )
    NotActions = @()
    DataActions = @()
    NotDataActions = @()
    AssignableScopes = @($subscriptionScope)
}

$roleDefinitionPath = Join-Path `
    ([System.IO.Path]::GetTempPath()) `
    "genie-platform-gateway-deployer-$([guid]::NewGuid().ToString('N')).json"
try {
    $roleDefinition | ConvertTo-Json -Depth 10 | Set-Content `
        -Path $roleDefinitionPath `
        -Encoding utf8

    $customRoles = az role definition list `
        --subscription $SubscriptionId `
        --custom-role-only true `
        --only-show-errors `
        -o json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to query the '$RoleName' role definition."
    }
    $existingRole = @($customRoles | Where-Object { $_.roleName -eq $RoleName })
    if (@($existingRole).Count -eq 0) {
        $savedRole = & az role definition create `
            --subscription $SubscriptionId `
            --role-definition $roleDefinitionPath `
            --only-show-errors `
            -o json | ConvertFrom-Json
    }
    else {
        $updateRoleDefinition = @{
            properties = @{
                roleName = $RoleName
                description = $roleDefinition.Description
                type = "CustomRole"
                permissions = @(
                    @{
                        actions = $roleDefinition.Actions
                        notActions = @()
                        dataActions = @()
                        notDataActions = @()
                    }
                )
                assignableScopes = @($subscriptionScope)
            }
        }
        $updateRoleDefinition | ConvertTo-Json -Depth 10 | Set-Content `
            -Path $roleDefinitionPath `
            -Encoding utf8
        $savedRole = & az rest `
            --method put `
            --url "https://management.azure.com$($existingRole[0].id)?api-version=2022-04-01" `
            --body "@$roleDefinitionPath" `
            --only-show-errors `
            -o json | ConvertFrom-Json
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create or update the '$RoleName' role definition."
    }
    $roleDefinitionId = $savedRole.id
    if ([string]::IsNullOrWhiteSpace($roleDefinitionId)) {
        $customRoles = az role definition list `
            --subscription $SubscriptionId `
            --custom-role-only true `
            --only-show-errors `
            -o json | ConvertFrom-Json
        $roleDefinitionId = @(
            $customRoles | Where-Object { $_.roleName -eq $RoleName }
        )[0].id
        if ([string]::IsNullOrWhiteSpace($roleDefinitionId)) {
            throw "Azure returned no id for the '$RoleName' role definition."
        }
    }
    $existingAssignment = az role assignment list `
        --subscription $SubscriptionId `
        --assignee-object-id $PrincipalObjectId `
        --role $roleDefinitionId `
        --scope $resourceGroupScope `
        --only-show-errors `
        -o json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to query the platform gateway role assignment."
    }
    if (@($existingAssignment).Count -eq 0) {
        & az role assignment create `
            --subscription $SubscriptionId `
            --assignee-object-id $PrincipalObjectId `
            --assignee-principal-type ServicePrincipal `
            --role $roleDefinitionId `
            --scope $resourceGroupScope `
            --only-show-errors `
            -o none
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to assign '$RoleName' at '$resourceGroupScope'."
        }
    }
}
finally {
    if (Test-Path $roleDefinitionPath) {
        Remove-Item -Path $roleDefinitionPath -Force
    }
}

[pscustomobject]@{
    principalObjectId = $PrincipalObjectId
    role = $RoleName
    scope = $resourceGroupScope
} | ConvertTo-Json -Compress