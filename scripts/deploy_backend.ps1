<#
.SYNOPSIS
    Atomically deploys the anonymous internal Genie FastAPI backend.

.DESCRIPTION
    Preserves the existing Container App identity, environment, scaling, and
    Azure service configuration; removes retired authentication sidecars; and
    moves external ingress directly to FastAPI port 8000.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SubscriptionId,

    [Parameter(Mandatory = $true)]
    [string]$ResourceGroup,

    [Parameter(Mandatory = $true)]
    [string]$ContainerAppName,

    [Parameter(Mandatory = $true)]
    [string]$BackendImage,

    [Parameter(Mandatory = $true)]
    [string]$AllowedOrigin,

    [Parameter(Mandatory = $true)]
    [string]$MemoryStoreEndpoint,

    [Parameter(Mandatory = $true)]
    [string]$PrototypeApiGatewayPublisherEmail,

    [Parameter(Mandatory = $true)]
    [string]$PrototypeApiGatewayPublisherName,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-z0-9-]{1,10}$')]
    [string]$RevisionSuffix,

    [string]$BackendContainerName = "genie-backend",
    [int]$WaitTimeoutSeconds = 600
)

$ErrorActionPreference = "Stop"

function Invoke-AzJson {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)

    $output = & az @Arguments --only-show-errors -o json
    if ($LASTEXITCODE -ne 0) {
        throw "Azure CLI command failed: az $($Arguments -join ' ')"
    }
    return $output | ConvertFrom-Json -Depth 100
}

function Remove-ContainerEnvironmentVariable {
    param(
        [Parameter(Mandatory = $true)]$Container,
        [Parameter(Mandatory = $true)][string]$Name
    )

    $Container.env = @($Container.env | Where-Object { $_.name -ne $Name })
}

function Set-ContainerEnvironmentVariable {
    param(
        [Parameter(Mandatory = $true)]$Container,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Value
    )

    Remove-ContainerEnvironmentVariable -Container $Container -Name $Name
    $Container.env = @($Container.env) + @(
        [pscustomobject]@{ name = $Name; value = $Value }
    )
}

foreach ($requiredValue in @{
    BackendImage = $BackendImage
    AllowedOrigin = $AllowedOrigin
    MemoryStoreEndpoint = $MemoryStoreEndpoint
    PrototypeApiGatewayPublisherEmail = $PrototypeApiGatewayPublisherEmail
    PrototypeApiGatewayPublisherName = $PrototypeApiGatewayPublisherName
}.GetEnumerator()) {
    if ([string]::IsNullOrWhiteSpace($requiredValue.Value)) {
        throw "$($requiredValue.Key) cannot be blank."
    }
}

$app = Invoke-AzJson containerapp show `
    --subscription $SubscriptionId `
    --resource-group $ResourceGroup `
    --name $ContainerAppName

$backend = @($app.properties.template.containers | Where-Object {
    $_.name -eq $BackendContainerName
})
if ($backend.Count -ne 1) {
    throw "Expected exactly one '$BackendContainerName' container; found $($backend.Count)."
}
$backend = $backend[0]
$backend.image = $BackendImage
foreach ($retiredName in @(
    "GENIE_MISE_ENDPOINT",
    "GENIE_ENTRA_AUTHORITY",
    "GENIE_ENTRA_TENANT_ID",
    "GENIE_ENTRA_CLIENT_ID",
    "GENIE_PROTOTYPE_MISE_ENABLED",
    "GENIE_PROTOTYPE_MISE_GATEWAY_IMAGE",
    "GENIE_PROTOTYPE_MISE_TEST_PRINCIPAL_CLIENT_ID",
    "GENIE_PROTOTYPE_TEST_PRINCIPAL_CLIENT_ID",
    "GENIE_PROTOTYPE_AUTHENTICATION_MODE",
    "GENIE_PROTOTYPE_SHARED_APPLICATION_OBJECT_ID",
    "GENIE_PROTOTYPE_SHARED_SERVICE_PRINCIPAL_OBJECT_ID",
    "GENIE_PROTOTYPE_SHARED_CLIENT_ID",
    "GENIE_PROTOTYPE_SHARED_DELEGATED_SCOPE",
    "GENIE_PROTOTYPE_SHARED_APPLICATION_ROLE_ID",
    "GENIE_PROTOTYPE_SHARED_FRONTEND_DOMAIN",
    "GENIE_PROTOTYPE_SHARED_SLOT_COUNT"
)) {
    Remove-ContainerEnvironmentVariable -Container $backend -Name $retiredName
}
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_CORS_ALLOWED_ORIGINS" -Value $AllowedOrigin
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_PROTOTYPE_API_GATEWAY_ENABLED" -Value "true"
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_PROTOTYPE_API_GATEWAY_PUBLISHER_EMAIL" -Value $PrototypeApiGatewayPublisherEmail
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_PROTOTYPE_API_GATEWAY_PUBLISHER_NAME" -Value $PrototypeApiGatewayPublisherName
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_MEMORY_STORE_BACKEND" -Value "cosmos_db"
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_MEMORY_STORE_ENDPOINT" -Value $MemoryStoreEndpoint
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_MEMORY_STORE_DATABASE_NAME" -Value "genie"
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_MEMORY_STORE_CONTAINER_NAME" -Value "memory"
$backend.probes = @(
    [pscustomobject]@{
        type = "Liveness"
        httpGet = [pscustomobject]@{ path = "/health/live"; port = 8000; scheme = "HTTP" }
        initialDelaySeconds = 10
        periodSeconds = 30
        timeoutSeconds = 5
        failureThreshold = 3
    }
    [pscustomobject]@{
        type = "Readiness"
        httpGet = [pscustomobject]@{ path = "/health/ready"; port = 8000; scheme = "HTTP" }
        initialDelaySeconds = 10
        periodSeconds = 10
        timeoutSeconds = 5
        failureThreshold = 6
    }
)

$otherContainers = @($app.properties.template.containers | Where-Object {
    $_.name -ne $BackendContainerName -and
    $_.name -ne "genie-auth-gateway" -and
    $_.name -ne "mise-sidecar"
})
$patch = @{
    properties = @{
        configuration = @{
            ingress = @{
                targetPort = 8000
            }
        }
        template = @{
            revisionSuffix = $RevisionSuffix
            containers = @($backend) + $otherContainers
        }
    }
}

$patchFile = Join-Path ([System.IO.Path]::GetTempPath()) "genie-backend-$([guid]::NewGuid().ToString('N')).json"
try {
    $patch | ConvertTo-Json -Depth 100 | Set-Content -Path $patchFile -Encoding utf8
    & az rest `
        --method patch `
        --uri "$($app.id)?api-version=2025-01-01" `
        --body "@$patchFile" `
        --only-show-errors `
        -o none
    if ($LASTEXITCODE -ne 0) {
        throw "Container App backend update failed."
    }
}
finally {
    if (Test-Path $patchFile) {
        Remove-Item -Path $patchFile -Force
    }
}

$deadline = (Get-Date).AddSeconds($WaitTimeoutSeconds)
do {
    Start-Sleep -Seconds 10
    $current = Invoke-AzJson containerapp show `
        --subscription $SubscriptionId `
        --resource-group $ResourceGroup `
        --name $ContainerAppName
    $ready = (
        $current.properties.latestRevisionName -eq $current.properties.latestReadyRevisionName -and
        $current.properties.latestRevisionName.EndsWith("-$RevisionSuffix") -and
        $current.properties.runningStatus -eq "Running"
    )
} while (-not $ready -and (Get-Date) -lt $deadline)

if (-not $ready) {
    throw "The backend revision did not become ready within $WaitTimeoutSeconds seconds."
}

$deployedBackend = @($current.properties.template.containers | Where-Object {
    $_.name -eq $BackendContainerName
})
if ($deployedBackend.Count -ne 1 -or $deployedBackend[0].image -ne $BackendImage) {
    throw "Backend image verification failed after deployment."
}
$retiredGateways = @($current.properties.template.containers | Where-Object {
    $_.name -in @("genie-auth-gateway", "mise-sidecar")
})
if ($retiredGateways.Count -ne 0) {
    throw "A retired authentication gateway is still deployed."
}
if ($current.properties.configuration.ingress.targetPort -ne 8000) {
    throw "External ingress does not target FastAPI."
}

$baseUri = "https://$($current.properties.configuration.ingress.fqdn)"
$health = Invoke-RestMethod -Method Get -Uri "$baseUri/health/ready" -TimeoutSec 30
if ($health.status -ne "ready") {
    throw "Backend readiness verification failed."
}
$sessionsResponse = Invoke-WebRequest -Method Get -Uri "$baseUri/sessions" -TimeoutSec 30
if ($sessionsResponse.StatusCode -ne 200) {
    throw "Anonymous API verification returned HTTP $($sessionsResponse.StatusCode)."
}

[pscustomobject]@{
    containerApp = $current.name
    revision = $current.properties.latestReadyRevisionName
    runningStatus = $current.properties.runningStatus
    ingressTargetPort = $current.properties.configuration.ingress.targetPort
    backendImage = $deployedBackend[0].image
    anonymousApi = "verified"
    health = $health.status
} | ConvertTo-Json -Depth 10
