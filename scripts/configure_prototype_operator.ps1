<#
.SYNOPSIS
    Grants the Genie runtime managed identity least-privilege rights to own
    isolated prototype resource groups and create their Container Apps environments.

.DESCRIPTION
    Creates or updates the subscription-scoped custom role used for dynamic
    genie-proto-* resource groups. Built-in Container Apps Contributor can
    manage Container Apps but does not grant Microsoft.App/managedEnvironments/write.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SubscriptionId,

    [Parameter(Mandatory = $true)]
    [string]$PrincipalObjectId,

    [string]$RoleName = "Genie Prototype Resource Group Operator"
)

$ErrorActionPreference = "Stop"

$subscriptionScope = "/subscriptions/$SubscriptionId"
$roleDefinition = @{
    Name = $RoleName
    IsCustom = $true
    Description = "Owns isolated Genie prototype resource groups and creates their private Container Apps environments."
    Actions = @(
        "Microsoft.Resources/subscriptions/resourceGroups/read"
        "Microsoft.Resources/subscriptions/resourceGroups/write"
        "Microsoft.Resources/subscriptions/resourceGroups/delete"
        "Microsoft.App/managedEnvironments/read"
        "Microsoft.App/managedEnvironments/write"
        "Microsoft.App/locations/managedEnvironmentOperationResults/read"
        "Microsoft.App/locations/managedEnvironmentOperationStatuses/read"
        "Microsoft.App/locations/operationResults/read"
        "Microsoft.App/locations/operationStatuses/read"
    )
    NotActions = @()
    DataActions = @()
    NotDataActions = @()
    AssignableScopes = @($subscriptionScope)
}

$roleDefinitionPath = Join-Path `
    ([System.IO.Path]::GetTempPath()) `
    "genie-prototype-operator-$([guid]::NewGuid().ToString('N')).json"
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
        $roleDefinitionId = @(
            az role definition list `
                --subscription $SubscriptionId `
                --custom-role-only true `
                --only-show-errors `
                -o json | ConvertFrom-Json | Where-Object { $_.roleName -eq $RoleName }
        )[0].id
    }
    if ([string]::IsNullOrWhiteSpace($roleDefinitionId)) {
        throw "Azure returned no id for the '$RoleName' role definition."
    }

    $savedActions = if ($null -ne $savedRole.properties) {
        @($savedRole.properties.permissions[0].actions)
    }
    else {
        @($savedRole.permissions[0].actions)
    }
    $missingActions = @($roleDefinition.Actions | Where-Object { $_ -notin $savedActions })
    if ($missingActions.Count -gt 0) {
        throw "Azure did not persist required '$RoleName' actions: $($missingActions -join ', ')."
    }

    $existingAssignment = az role assignment list `
        --subscription $SubscriptionId `
        --assignee-object-id $PrincipalObjectId `
        --role $roleDefinitionId `
        --scope $subscriptionScope `
        --only-show-errors `
        -o json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to query the prototype operator role assignment."
    }
    if (@($existingAssignment).Count -eq 0) {
        & az role assignment create `
            --subscription $SubscriptionId `
            --assignee-object-id $PrincipalObjectId `
            --assignee-principal-type ServicePrincipal `
            --role $roleDefinitionId `
            --scope $subscriptionScope `
            --only-show-errors `
            -o none
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to assign '$RoleName' at '$subscriptionScope'."
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
    scope = $subscriptionScope
} | ConvertTo-Json -Compress