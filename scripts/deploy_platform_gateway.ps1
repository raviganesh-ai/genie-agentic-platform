<#
.SYNOPSIS
    Publishes Genie through API Management and makes its Container Apps
    environment private.

.DESCRIPTION
    Provisions the public Standard v2 API Management edge with outbound VNet
    integration, verifies it against the current backend, creates the Container
    Apps private endpoint and DNS zone, disables environment public access, and
    then verifies both the private gateway route and direct-route denial. Use
    PrepareOnly to stop after APIM verification so the frontend can be switched
    to the gateway before the private-backend cutover.
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
    [string]$AllowedOrigin,

    [Parameter(Mandatory = $true)]
    [string]$PublisherEmail,

    [Parameter(Mandatory = $true)]
    [string]$PublisherName,

    [switch]$PrepareOnly,

    [int]$WaitTimeoutSeconds = 1800
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

function Wait-ForGatewayReadiness {
    param(
        [Parameter(Mandatory = $true)][string]$GatewayUrl,
        [Parameter(Mandatory = $true)][datetime]$Deadline
    )

    do {
        try {
            $health = Invoke-RestMethod `
                -Method Get `
                -Uri "$GatewayUrl/health/ready" `
                -TimeoutSec 30
            if ($health.status -eq "ready") {
                return
            }
        }
        catch {
            Write-Host "Gateway route is not ready yet: $($_.Exception.Message)"
        }
        Start-Sleep -Seconds 15
    } while ((Get-Date) -lt $Deadline)

    throw "API Management did not reach the Genie readiness endpoint within $WaitTimeoutSeconds seconds."
}

foreach ($requiredValue in @{
    SubscriptionId = $SubscriptionId
    ResourceGroup = $ResourceGroup
    ContainerAppName = $ContainerAppName
    AllowedOrigin = $AllowedOrigin
    PublisherEmail = $PublisherEmail
    PublisherName = $PublisherName
}.GetEnumerator()) {
    if ([string]::IsNullOrWhiteSpace($requiredValue.Value)) {
        throw "$($requiredValue.Key) cannot be blank."
    }
}
if (-not $AllowedOrigin.StartsWith("https://", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "AllowedOrigin must use HTTPS."
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$templateFile = Join-Path $repoRoot "infra/platform-private-gateway.bicep"
$app = Invoke-AzJson containerapp show `
    --subscription $SubscriptionId `
    --resource-group $ResourceGroup `
    --name $ContainerAppName
$environment = Invoke-AzJson containerapp env show `
    --subscription $SubscriptionId `
    --ids $app.properties.environmentId
$infrastructureSubnetId = $environment.properties.vnetConfiguration.infrastructureSubnetId
if ([string]::IsNullOrWhiteSpace($infrastructureSubnetId)) {
    throw "The Container Apps environment is not VNet integrated."
}
$subnetParts = $infrastructureSubnetId -split "/"
$virtualNetworksIndex = [array]::IndexOf($subnetParts, "virtualNetworks")
if ($virtualNetworksIndex -lt 0 -or $virtualNetworksIndex + 1 -ge $subnetParts.Count) {
    throw "Could not determine the virtual network from '$infrastructureSubnetId'."
}
$virtualNetworkName = $subnetParts[$virtualNetworksIndex + 1]
$deploymentName = "genie-platform-private-gateway"
$commonParameters = @(
    "containerAppName=$ContainerAppName",
    "containerAppsEnvironmentName=$($environment.name)",
    "virtualNetworkName=$virtualNetworkName",
    "publisherEmail=$PublisherEmail",
    "publisherName=$PublisherName",
    "allowedOrigin=$($AllowedOrigin.TrimEnd('/'))"
)

Write-Host "Provisioning the public API Management edge..." -ForegroundColor Cyan
& az deployment group create `
    --subscription $SubscriptionId `
    --resource-group $ResourceGroup `
    --name $deploymentName `
    --template-file $templateFile `
    --parameters @commonParameters enablePrivateEndpoint=false `
    --only-show-errors `
    -o none
if ($LASTEXITCODE -ne 0) {
    throw "API Management edge deployment failed."
}
$deployment = Invoke-AzJson deployment group show `
    --subscription $SubscriptionId `
    --resource-group $ResourceGroup `
    --name $deploymentName
$gatewayUrl = $deployment.properties.outputs.gatewayUrl.value.TrimEnd("/")
$deadline = (Get-Date).AddSeconds($WaitTimeoutSeconds)
Wait-ForGatewayReadiness -GatewayUrl $gatewayUrl -Deadline $deadline

if ($PrepareOnly) {
    [pscustomobject]@{
        apiManagement = $deployment.properties.outputs.apiManagementName.value
        gatewayUrl = $gatewayUrl
        containerAppsEnvironment = $environment.name
        publicNetworkAccess = $environment.properties.publicNetworkAccess
        gatewayRoute = "verified"
        cutover = "pending"
    } | ConvertTo-Json -Depth 10 -Compress
    return
}

Write-Host "Creating the Container Apps private endpoint and DNS integration..." -ForegroundColor Cyan
& az deployment group create `
    --subscription $SubscriptionId `
    --resource-group $ResourceGroup `
    --name $deploymentName `
    --template-file $templateFile `
    --parameters @commonParameters enablePrivateEndpoint=true `
    --only-show-errors `
    -o none
if ($LASTEXITCODE -ne 0) {
    throw "Container Apps private endpoint deployment failed; public access was not changed."
}

Write-Host "Disabling public access to the Container Apps environment..." -ForegroundColor Cyan
& az containerapp env update `
    --subscription $SubscriptionId `
    --ids $environment.id `
    --public-network-access Disabled `
    --only-show-errors `
    -o none
if ($LASTEXITCODE -ne 0) {
    throw "Failed to disable Container Apps environment public access."
}

$privateEndpointName = "$($environment.name)-private-endpoint"
$privateEndpoint = Invoke-AzJson network private-endpoint show `
    --subscription $SubscriptionId `
    --resource-group $ResourceGroup `
    --name $privateEndpointName
$connectionStatuses = @(
    $privateEndpoint.properties.privateLinkServiceConnections |
        ForEach-Object { $_.properties.privateLinkServiceConnectionState.status }
)
if ($connectionStatuses.Count -eq 0 -or @($connectionStatuses | Where-Object { $_ -ne "Approved" }).Count -ne 0) {
    throw "The Container Apps private endpoint connection is not approved."
}

$environment = Invoke-AzJson containerapp env show `
    --subscription $SubscriptionId `
    --ids $environment.id
if ($environment.properties.publicNetworkAccess -ne "Disabled") {
    throw "Container Apps environment public access is not disabled."
}
$deadline = (Get-Date).AddSeconds($WaitTimeoutSeconds)
Wait-ForGatewayReadiness -GatewayUrl $gatewayUrl -Deadline $deadline

$directBackendUrl = "https://$($app.properties.configuration.ingress.fqdn)"
$directRouteExposed = $false
try {
    $directResponse = Invoke-WebRequest `
        -Method Get `
        -Uri "$directBackendUrl/health/ready" `
        -TimeoutSec 30 `
        -SkipHttpErrorCheck
    $directRouteExposed = $directResponse.StatusCode -ge 200 -and $directResponse.StatusCode -lt 300
}
catch {
    Write-Host "Direct backend route is unreachable as required: $($_.Exception.Message)"
}
if ($directRouteExposed) {
    throw "Direct Container Apps ingress still accepts public requests."
}

[pscustomobject]@{
    apiManagement = $deployment.properties.outputs.apiManagementName.value
    gatewayUrl = $gatewayUrl
    containerAppsEnvironment = $environment.name
    publicNetworkAccess = $environment.properties.publicNetworkAccess
    privateEndpoint = $privateEndpoint.name
    gatewayRoute = "verified"
    directBackendRoute = "denied"
} | ConvertTo-Json -Depth 10 -Compress