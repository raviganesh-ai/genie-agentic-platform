<#
.SYNOPSIS
    Provisions Genie's foundational Azure infrastructure into an empty
    Azure subscription, gated by a mandatory deployment-readiness check.

.DESCRIPTION
    Implements "Deployment readiness validation must occur before
    infrastructure provisioning" (see .github/copilot-instructions.md):

      1. Runs scripts/validate_deployment_readiness.py. If any required
         Azure resource provider is not registered, this script aborts
         WITHOUT attempting any deployment (fail closed, no fallback).
      2. Only if step 1 exits 0, deploys infra/main.bicep at subscription
         scope.

.PARAMETER EnvironmentName
    Short, unique name for this Genie environment (e.g. "dev", "prod").

.PARAMETER Location
    Azure region to deploy every resource into.

.PARAMETER SubscriptionId
    Azure subscription id to deploy into. Falls back to
    $env:GENIE_DEPLOY_SUBSCRIPTION_ID if not supplied.

.EXAMPLE
    ./scripts/deploy_infra.ps1 -EnvironmentName dev -Location eastus2 -SubscriptionId <sub-id>
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$EnvironmentName,

    [Parameter(Mandatory = $true)]
    [string]$Location,

    [Parameter(Mandatory = $false)]
    [string]$AiSearchLocation,

    [Parameter(Mandatory = $false)]
    [string]$SubscriptionId = $env:GENIE_DEPLOY_SUBSCRIPTION_ID
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($SubscriptionId)) {
    Write-Error "SubscriptionId is required (pass -SubscriptionId or set GENIE_DEPLOY_SUBSCRIPTION_ID)."
    exit 1
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $repoRoot "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    $pythonExe = "python"
}

Write-Host "== Step 1/2: deployment readiness validation ==" -ForegroundColor Cyan
$env:GENIE_DEPLOY_SUBSCRIPTION_ID = $SubscriptionId
Push-Location $repoRoot
try {
    & $pythonExe "scripts/validate_deployment_readiness.py"
    $readinessExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

if ($readinessExitCode -ne 0) {
    Write-Error "Deployment readiness check failed (exit code $readinessExitCode). Aborting - infrastructure will NOT be provisioned."
    exit $readinessExitCode
}

Write-Host "== Step 2/2: provisioning infrastructure ==" -ForegroundColor Cyan
$deployParams = "environmentName=$EnvironmentName", "location=$Location"
if (-not [string]::IsNullOrWhiteSpace($AiSearchLocation)) {
    $deployParams += "aiSearchLocation=$AiSearchLocation"
}

az deployment sub create `
    --name "genie-$EnvironmentName-$(Get-Date -Format 'yyyyMMddHHmmss')" `
    --location $Location `
    --subscription $SubscriptionId `
    --template-file (Join-Path $repoRoot "infra\main.bicep") `
    --parameters @deployParams

exit $LASTEXITCODE
