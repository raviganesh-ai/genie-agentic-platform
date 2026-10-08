<#
.SYNOPSIS
    Atomically deploys the Genie FastAPI backend.

.DESCRIPTION
    Preserves the existing Container App identity, environment, scaling, and
    Azure service configuration; removes retired authentication sidecars; and
    moves ingress to FastAPI port 8000. Readiness is verified only through the
    platform API Management gateway because the backend environment is private.
    Also wires first-party authentication (app.security.auth_service) and
    verifies that unauthenticated requests are actually rejected.
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
    [string]$AcrAgentPoolName,

    [Parameter(Mandatory = $true)]
    [string]$AllowedOrigin,

    [Parameter(Mandatory = $true)]
    [string]$GatewayUrl,

    [Parameter(Mandatory = $true)]
    [string]$MemoryStoreEndpoint,

    [Parameter(Mandatory = $true)]
    [string]$GitHubMcpEndpoint,

    [Parameter(Mandatory = $true)]
    [string]$GitHubMcpTokenSecretName,

    [Parameter(Mandatory = $true)]
    [string]$ApimSubscriptionKey,

    [Parameter(Mandatory = $true)]
    [string]$AuthTokenSigningKeySecretName,

    [Parameter(Mandatory = $true)]
    [string]$AuthUsersSecretName,

    [Parameter(Mandatory = $true)]
    [string]$PrototypeApiGatewayPublisherEmail,

    [Parameter(Mandatory = $true)]
    [string]$PrototypeApiGatewayPublisherName,

    [Parameter(Mandatory = $true)]
    [ValidateRange(0, 20)]
    [int]$PrototypeMaxActivePerOwner,

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

function Set-ContainerSecretEnvironmentVariable {
    param(
        [Parameter(Mandatory = $true)]$Container,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$SecretRef
    )

    Remove-ContainerEnvironmentVariable -Container $Container -Name $Name
    $Container.env = @($Container.env) + @(
        [pscustomobject]@{ name = $Name; secretRef = $SecretRef }
    )
}

foreach ($requiredValue in @{
    BackendImage = $BackendImage
    AcrAgentPoolName = $AcrAgentPoolName
    AllowedOrigin = $AllowedOrigin
    GatewayUrl = $GatewayUrl
    MemoryStoreEndpoint = $MemoryStoreEndpoint
    GitHubMcpEndpoint = $GitHubMcpEndpoint
    GitHubMcpTokenSecretName = $GitHubMcpTokenSecretName
    PrototypeApiGatewayPublisherEmail = $PrototypeApiGatewayPublisherEmail
    PrototypeApiGatewayPublisherName = $PrototypeApiGatewayPublisherName
}.GetEnumerator()) {
    if ([string]::IsNullOrWhiteSpace($requiredValue.Value)) {
        throw "$($requiredValue.Key) cannot be blank."
    }
}
if (-not $GatewayUrl.StartsWith("https://", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "GatewayUrl must use HTTPS."
}
if (-not $GitHubMcpEndpoint.StartsWith("https://", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "GitHubMcpEndpoint must use HTTPS."
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
$backend.PSObject.Properties.Remove("imageType")
$backend.image = $BackendImage
foreach ($retiredName in @(
    "GENIE_MISE_ENDPOINT",
    "GENIE_ENTRA_ENABLED",
    "GENIE_ENTRA_AUTHORITY",
    "GENIE_ENTRA_TENANT_ID",
    "GENIE_ENTRA_CLIENT_ID",
    "GENIE_ENTRA_AUDIENCE",
    "GENIE_ENTRA_REQUIRED_SCOPE",
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
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_GITHUB_MCP_ENABLED" -Value "true"
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_GITHUB_MCP_ENDPOINT" -Value $GitHubMcpEndpoint
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_GITHUB_MCP_TOKEN_ENV_VAR" -Value "GITHUB_MCP_TOKEN"
Set-ContainerSecretEnvironmentVariable -Container $backend -Name "GITHUB_MCP_TOKEN" -SecretRef $GitHubMcpTokenSecretName
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_AUTH_ENABLED" -Value "true"
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_AUTH_TOKEN_SIGNING_KEY_ENV_VAR" -Value "GENIE_AUTH_TOKEN_SIGNING_KEY"
Set-ContainerSecretEnvironmentVariable -Container $backend -Name "GENIE_AUTH_TOKEN_SIGNING_KEY" -SecretRef $AuthTokenSigningKeySecretName
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_AUTH_USERS_ENV_VAR" -Value "GENIE_AUTH_USERS"
Set-ContainerSecretEnvironmentVariable -Container $backend -Name "GENIE_AUTH_USERS" -SecretRef $AuthUsersSecretName
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_PROTOTYPE_API_GATEWAY_ENABLED" -Value "true"
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_PROTOTYPE_API_GATEWAY_PUBLISHER_EMAIL" -Value $PrototypeApiGatewayPublisherEmail
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_PROTOTYPE_API_GATEWAY_PUBLISHER_NAME" -Value $PrototypeApiGatewayPublisherName
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_MEMORY_STORE_BACKEND" -Value "cosmos_db"
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_DEPLOYMENT_ACR_AGENT_POOL_NAME" -Value $AcrAgentPoolName
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_MEMORY_STORE_ENDPOINT" -Value $MemoryStoreEndpoint
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_MEMORY_STORE_DATABASE_NAME" -Value "genie"
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_MEMORY_STORE_CONTAINER_NAME" -Value "memory"
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_LINEAGE_STORE_BACKEND" -Value "cosmos_db"
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_LINEAGE_STORE_ENDPOINT" -Value $MemoryStoreEndpoint
Set-ContainerEnvironmentVariable -Container $backend -Name "GENIE_PROTOTYPE_MAX_ACTIVE_PER_OWNER" -Value $PrototypeMaxActivePerOwner.ToString([System.Globalization.CultureInfo]::InvariantCulture)
$probes = @(
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
$backend | Add-Member -MemberType NoteProperty -Name probes -Value $probes -Force

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
    throw "Container Apps ingress does not target FastAPI."
}

$environment = Invoke-AzJson containerapp env show `
    --subscription $SubscriptionId `
    --ids $current.properties.environmentId
if ($environment.properties.publicNetworkAccess -ne "Disabled") {
    throw "Container Apps environment public access must be disabled."
}

$baseUri = $GatewayUrl.TrimEnd("/")
$apimHeaders = @{ "Ocp-Apim-Subscription-Key" = $ApimSubscriptionKey }
$health = Invoke-RestMethod -Method Get -Uri "$baseUri/health/ready" -Headers $apimHeaders -TimeoutSec 30
if ($health.status -ne "ready") {
    throw "Backend readiness verification failed."
}
# First-party authentication (app.security.auth_service) is always enabled
# in this deployment - prove the fix for the unauthenticated-IDOR incident
# actually took effect: a request with a valid gateway subscription key but
# no bearer token must still be rejected, not served.
$sessionsResponse = Invoke-WebRequest -Method Get -Uri "$baseUri/sessions" -Headers $apimHeaders -TimeoutSec 30 -SkipHttpErrorCheck
if ($sessionsResponse.StatusCode -ne 401) {
    throw "Expected unauthenticated '/sessions' access to be rejected with HTTP 401, got HTTP $($sessionsResponse.StatusCode)."
}

# The backend must be reachable ONLY through the platform APIM. Prove the
# container app's own direct FQDN (the public default domain Container Apps
# always assigns) is denied - this is the only automated regression check
# for the private-endpoints subnet NSG that enforces that boundary, and it
# catches it regardless of exactly how the isolation is implemented.
$directFqdn = $current.properties.configuration.ingress.fqdn
$directIngressDenied = $false
try {
    Invoke-WebRequest -Method Get -Uri "https://$directFqdn/health/live" -TimeoutSec 15 -ErrorAction Stop | Out-Null
}
catch {
    $directIngressDenied = $true
}
if (-not $directIngressDenied) {
    throw "Direct backend ingress at https://$directFqdn is reachable; only the platform APIM must be able to reach the backend."
}

[pscustomobject]@{
    containerApp = $current.name
    revision = $current.properties.latestReadyRevisionName
    runningStatus = $current.properties.runningStatus
    ingressTargetPort = $current.properties.configuration.ingress.targetPort
    backendImage = $deployedBackend[0].image
    gatewayUrl = $baseUri
    publicNetworkAccess = $environment.properties.publicNetworkAccess
    unauthenticatedAccessRejected = "verified"
    directIngressDenied = $directIngressDenied
    health = $health.status
} | ConvertTo-Json -Depth 10
