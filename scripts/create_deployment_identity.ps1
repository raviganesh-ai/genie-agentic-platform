<#
.SYNOPSIS
    Creates a least-privilege, deployment-only Azure identity for
    provisioning Genie's infrastructure - deliberately NOT the caller's own
    Owner role, per the Security Requirements ("least privilege") in
    .github/copilot-instructions.md.

.DESCRIPTION
    One-time bootstrap, run by whoever currently holds sufficient privilege
    (e.g. subscription Owner) on a brand-new subscription:

      1. Renders a custom RBAC role definition ("Genie Infrastructure
         Deployer") scoped to exactly the Azure resource provider
         namespaces Genie's infra/ depends on (never Owner/Contributor).
      2. Creates (or updates) that role definition on the subscription.
      3. Attempts to create a dedicated Microsoft Entra ID service
         principal and assign the custom role to it, so deployments can
         run unattended without anyone's personal login.
      4. If step 3 fails - many tenants (including Microsoft-internal
         ones) block password/self-signed-certificate app credentials via
         tenant policy - falls back to assigning the custom role directly
         to the caller's own signed-in account instead. The caller's
         Owner role assignment (if any) is left untouched; deployments
         simply no longer *need* it, since the custom role alone is
         sufficient to run infra/main.bicep.

    Credentials (if a service principal was created) are printed as
    environment variables to export locally - NEVER written into any file
    this repository tracks (.env* is gitignored).

.PARAMETER SubscriptionId
    Target Azure subscription id.

.PARAMETER ServicePrincipalName
    Display name for the new service principal. Defaults to
    "genie-deployment-sp".

.PARAMETER RoleName
    Name of the custom role to create/update. Defaults to
    "Genie Infrastructure Deployer".

.EXAMPLE
    ./scripts/create_deployment_identity.ps1 -SubscriptionId 00000000-0000-0000-0000-000000000000
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$SubscriptionId,

    [Parameter(Mandatory = $false)]
    [string]$ServicePrincipalName = "genie-deployment-sp",

    [Parameter(Mandatory = $false)]
    [string]$RoleName = "Genie Infrastructure Deployer"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $repoRoot "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    $pythonExe = "python"
}

$caller = az account show --query user.name -o tsv
Write-Host "Bootstrapping with caller identity: $caller" -ForegroundColor Yellow

Write-Host "`n== Step 1/3: rendering least-privilege role definition ==" -ForegroundColor Cyan
$roleDefinitionPath = Join-Path $repoRoot "infra\roles\.genie-deployment-role.generated.json"
New-Item -ItemType Directory -Force -Path (Split-Path $roleDefinitionPath) | Out-Null
Push-Location $repoRoot
try {
    & $pythonExe "scripts/generate_deployment_role_definition.py" `
        --subscription-id $SubscriptionId --role-name $RoleName `
        | Out-File -Encoding utf8 $roleDefinitionPath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to render role definition (exit code $LASTEXITCODE)."
    }
}
finally {
    Pop-Location
}

Write-Host "`n== Step 2/3: creating/updating the custom role ==" -ForegroundColor Cyan
$existingRole = az role definition list --name $RoleName --subscription $SubscriptionId -o json | ConvertFrom-Json
if ($existingRole -and $existingRole.Count -gt 0) {
    az role definition update --role-definition $roleDefinitionPath --subscription $SubscriptionId | Out-Null
    Write-Host "Updated existing custom role '$RoleName'."
}
else {
    az role definition create --role-definition $roleDefinitionPath --subscription $SubscriptionId | Out-Null
    Write-Host "Created custom role '$RoleName'."
}
$roleId = (az role definition list --name $RoleName --subscription $SubscriptionId --query "[0].name" -o tsv)
$roleScope = "/subscriptions/$SubscriptionId/providers/Microsoft.Authorization/roleDefinitions/$roleId"

Write-Host "`n== Step 3/3: granting the role to a deployment identity ==" -ForegroundColor Cyan
$spCreated = $false
try {
    $existingSp = az ad sp list --display-name $ServicePrincipalName -o json | ConvertFrom-Json
    if ($existingSp -and $existingSp.Count -gt 0) {
        throw "A service principal named '$ServicePrincipalName' already exists (appId $($existingSp[0].appId)); not creating a duplicate."
    }
    $spJson = az ad sp create-for-rbac --name $ServicePrincipalName -o json 2>$null | ConvertFrom-Json
    if (-not $spJson) {
        throw "az ad sp create-for-rbac failed (often blocked by tenant app-credential policy)."
    }
    az role assignment create --assignee $spJson.appId --role $roleScope --scope "/subscriptions/$SubscriptionId" | Out-Null
    $spCreated = $true

    Write-Host "`n== Done: service principal ==" -ForegroundColor Green
    Write-Host "Service principal '$ServicePrincipalName' (appId $($spJson.appId)) now holds ONLY the '$RoleName' role - not Owner, not Contributor."
    Write-Host "`nExport these for every future deployment (never commit them):" -ForegroundColor Yellow
    Write-Host "  `$env:AZURE_CLIENT_ID     = `"$($spJson.appId)`""
    Write-Host "  `$env:AZURE_CLIENT_SECRET = `"$($spJson.password)`""
    Write-Host "  `$env:AZURE_TENANT_ID     = `"$($spJson.tenant)`""
    Write-Host "  `$env:GENIE_DEPLOY_SUBSCRIPTION_ID = `"$SubscriptionId`""
}
catch {
    Write-Host "Service principal creation was not possible: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "Falling back to assigning the custom role directly to your own signed-in account ($caller)." -ForegroundColor Yellow

    $callerObjectId = az ad signed-in-user show --query id -o tsv
    az role assignment create `
        --assignee-object-id $callerObjectId `
        --assignee-principal-type User `
        --role $roleScope `
        --scope "/subscriptions/$SubscriptionId" | Out-Null

    Write-Host "`n== Done: role assigned to your account ==" -ForegroundColor Green
    Write-Host "'$caller' now also holds the '$RoleName' role on this subscription."
    Write-Host "Any pre-existing Owner assignment on this account was left untouched - deployments simply no longer need it; infra/main.bicep only requires the '$RoleName' role."
}

if (-not $spCreated) {
    Write-Host "`nSet GENIE_DEPLOY_SUBSCRIPTION_ID and continue using 'az login' as $caller to deploy - the '$RoleName' role (not Owner) is what authorizes scripts/deploy_infra.ps1 and scripts/validate_deployment_readiness.py."
}

