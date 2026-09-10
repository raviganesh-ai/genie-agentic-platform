<#
.SYNOPSIS
    Atomically deploys the Genie backend and MISE authentication gateway.

.DESCRIPTION
    Preserves the existing FastAPI container configuration, adds one gateway
    container to every Container App replica, and moves external ingress from
    FastAPI port 8000 to gateway port 8080. The gateway authenticates every API
    request and forwards the original bearer token to FastAPI for validation.
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
    [string]$ApplicationTenantId,

    [Parameter(Mandatory = $true)]
    [string]$ClientId,

    [Parameter(Mandatory = $true)]
    [string]$BackendImage,

    [Parameter(Mandatory = $true)]
    [string]$GatewayImage,

    [Parameter(Mandatory = $true)]
    [string]$AllowedOrigin,

    [Parameter(Mandatory = $true)]
    [string]$MemoryStoreEndpoint,

    [Parameter(Mandatory = $true)]
    [string]$SharedApplicationObjectId,

    [Parameter(Mandatory = $true)]
    [string]$SharedServicePrincipalObjectId,

    [Parameter(Mandatory = $true)]
    [string]$SharedApplicationRoleId,

    [Parameter(Mandatory = $true)]
    [string]$SharedFrontendDomain,

    [int]$SharedSlotCount = 50,

    [Parameter(Mandatory = $true)]
    [string]$DelegatedScope,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-z0-9-]{1,10}$')]
    [string]$RevisionSuffix,

    [string]$BackendContainerName = "genie-backend",
    [string]$GatewayContainerName = "genie-auth-gateway",
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
    ApplicationTenantId = $ApplicationTenantId
    ClientId = $ClientId
    BackendImage = $BackendImage
    GatewayImage = $GatewayImage
    AllowedOrigin = $AllowedOrigin
    MemoryStoreEndpoint = $MemoryStoreEndpoint
    SharedApplicationObjectId = $SharedApplicationObjectId
    SharedServicePrincipalObjectId = $SharedServicePrincipalObjectId
    SharedApplicationRoleId = $SharedApplicationRoleId
    SharedFrontendDomain = $SharedFrontendDomain
    DelegatedScope = $DelegatedScope
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
Remove-ContainerEnvironmentVariable -Container $backend -Name "GENIE_MISE_ENDPOINT"
$backendIdentityClientIds = @($backend.env | Where-Object {
    $_.name -eq "AZURE_CLIENT_ID" -and -not [string]::IsNullOrWhiteSpace($_.value)
})
if ($backendIdentityClientIds.Count -ne 1) {
    throw "Expected exactly one non-empty AZURE_CLIENT_ID on '$BackendContainerName'; found $($backendIdentityClientIds.Count)."
}
$backendIdentityClientId = $backendIdentityClientIds[0].value
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_PROTOTYPE_MISE_ENABLED" -Value "true"
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_PROTOTYPE_MISE_GATEWAY_IMAGE" -Value $GatewayImage
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_PROTOTYPE_MISE_TEST_PRINCIPAL_CLIENT_ID" -Value $backendIdentityClientId
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_ENTRA_AUTHORITY" -Value "https://login.microsoftonline.com/$ApplicationTenantId/v2.0"
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_ENTRA_TENANT_ID" -Value $ApplicationTenantId
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_ENTRA_CLIENT_ID" -Value $ClientId
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_MEMORY_STORE_BACKEND" -Value "cosmos_db"
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_MEMORY_STORE_ENDPOINT" -Value $MemoryStoreEndpoint
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_MEMORY_STORE_DATABASE_NAME" -Value "genie"
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_MEMORY_STORE_CONTAINER_NAME" -Value "memory"
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_PROTOTYPE_AUTHENTICATION_MODE" -Value "shared"
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_PROTOTYPE_SHARED_APPLICATION_OBJECT_ID" -Value $SharedApplicationObjectId
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_PROTOTYPE_SHARED_SERVICE_PRINCIPAL_OBJECT_ID" -Value $SharedServicePrincipalObjectId
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_PROTOTYPE_SHARED_CLIENT_ID" -Value $ClientId
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_PROTOTYPE_SHARED_DELEGATED_SCOPE" -Value $DelegatedScope
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_PROTOTYPE_SHARED_APPLICATION_ROLE_ID" -Value $SharedApplicationRoleId
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_PROTOTYPE_SHARED_FRONTEND_DOMAIN" -Value $SharedFrontendDomain
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_PROTOTYPE_SHARED_SLOT_COUNT" -Value $SharedSlotCount.ToString()

$gateway = [pscustomobject]@{
    name = $GatewayContainerName
    image = $GatewayImage
    resources = [pscustomobject]@{
        cpu = 0.5
        memory = "1Gi"
    }
    env = @(
        [pscustomobject]@{ name = "ASPNETCORE_HTTP_PORTS"; value = "8080" }
        [pscustomobject]@{ name = "AzureAd__Instance"; value = "https://login.microsoftonline.com/" }
        [pscustomobject]@{ name = "AzureAd__TenantId"; value = $ApplicationTenantId }
        [pscustomobject]@{ name = "AzureAd__ClientId"; value = $ClientId }
        [pscustomobject]@{ name = "AzureAd__Audiences__0"; value = $ClientId }
        [pscustomobject]@{ name = "AzureAd__Audiences__1"; value = "api://$ClientId" }
        [pscustomobject]@{ name = "Backend__Destination"; value = "http://localhost:8000" }
        [pscustomobject]@{ name = "Cors__AllowedOrigins__0"; value = $AllowedOrigin }
    )
    probes = @(
        [pscustomobject]@{
            type = "Liveness"
            httpGet = [pscustomobject]@{ path = "/health/live"; port = 8080; scheme = "HTTP" }
            initialDelaySeconds = 10
            periodSeconds = 30
            timeoutSeconds = 5
            failureThreshold = 3
        }
        [pscustomobject]@{
            type = "Readiness"
            httpGet = [pscustomobject]@{ path = "/health/ready"; port = 8080; scheme = "HTTP" }
            initialDelaySeconds = 10
            periodSeconds = 10
            timeoutSeconds = 5
            failureThreshold = 6
        }
    )
}

$otherContainers = @($app.properties.template.containers | Where-Object {
    $_.name -ne $BackendContainerName -and
    $_.name -ne $GatewayContainerName -and
    $_.name -ne "mise-sidecar"
})
$patch = @{
    properties = @{
        configuration = @{
            ingress = @{
                targetPort = 8080
            }
        }
        template = @{
            revisionSuffix = $RevisionSuffix
            containers = @($backend) + $otherContainers + @($gateway)
        }
    }
}

$patchFile = Join-Path ([System.IO.Path]::GetTempPath()) "genie-gateway-$([guid]::NewGuid().ToString('N')).json"
try {
    $patch | ConvertTo-Json -Depth 100 | Set-Content -Path $patchFile -Encoding utf8
    & az rest `
        --method patch `
        --uri "$($app.id)?api-version=2025-01-01" `
        --body "@$patchFile" `
        --only-show-errors `
        -o none
    if ($LASTEXITCODE -ne 0) {
        throw "Container App gateway update failed."
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
    throw "The gateway revision did not become ready within $WaitTimeoutSeconds seconds."
}

$deployedGateway = @($current.properties.template.containers | Where-Object {
    $_.name -eq $GatewayContainerName
})
if ($deployedGateway.Count -ne 1 -or $deployedGateway[0].image -ne $GatewayImage) {
    throw "Authentication gateway verification failed after deployment."
}
if ($current.properties.configuration.ingress.targetPort -ne 8080) {
    throw "External ingress does not target the authentication gateway."
}

$baseUri = "https://$($current.properties.configuration.ingress.fqdn)"
$health = Invoke-RestMethod -Method Get -Uri "$baseUri/health/ready" -TimeoutSec 30
if ($health.status -ne "ready") {
    throw "Gateway readiness verification failed."
}

$unauthenticatedStatus = 0
try {
    Invoke-WebRequest -Method Get -Uri "$baseUri/sessions" -TimeoutSec 30 | Out-Null
}
catch {
    $unauthenticatedStatus = [int]$_.Exception.Response.StatusCode
}
if ($unauthenticatedStatus -ne 401) {
    throw "Expected unauthenticated API traffic to return 401; received $unauthenticatedStatus."
}

[pscustomobject]@{
    containerApp = $current.name
    revision = $current.properties.latestReadyRevisionName
    runningStatus = $current.properties.runningStatus
    ingressTargetPort = $current.properties.configuration.ingress.targetPort
    backendImage = $backend.image
    gatewayImage = $deployedGateway[0].image
    unauthenticatedStatus = $unauthenticatedStatus
    health = $health.status
} | ConvertTo-Json -Depth 10