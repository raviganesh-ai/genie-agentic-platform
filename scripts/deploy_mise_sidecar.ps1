<#
.SYNOPSIS
    Adds or upgrades the MISE v2 sidecar on an existing Genie Container App.

.DESCRIPTION
    Synchronizes the pinned restricted MISE image into the deployment ACR, configures
    the exact Entra tenant, ClientId, audience, and bearer-token inbound policy,
    then atomically replaces the Container App's container template while
    preserving the existing backend container configuration.

    The operation fails before deployment if the image cannot be synchronized.
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
    [string]$AcrName,

    [Parameter(Mandatory = $true)]
    [string]$TenantId,

    [Parameter(Mandatory = $true)]
    [string]$ClientId,

    [string]$BackendContainerName = "genie-backend",
    [string]$BackendImage = "",
    [string]$MiseImageTag = "2.5.3-azurelinux3.0-distroless",
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

function Set-ContainerEnvironmentVariable {
    param(
        [Parameter(Mandatory = $true)]$Container,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Value
    )

    $existing = @($Container.env | Where-Object { $_.name -ne $Name })
    $Container.env = @($existing + [pscustomobject]@{ name = $Name; value = $Value })
}

$sourceRepository = "mcr.microsoft.com/msftonly/mise/mise-1p-container-image"
$targetRepository = "mise/mise-1p-container-image"
$targetImage = "$targetRepository`:$MiseImageTag"
$cacheRuleName = "mise-sync"

$manifest = Invoke-AzJson ad app show --id $ClientId
$idtypClaim = @($manifest.optionalClaims.accessToken | Where-Object {
    $_.name -eq "idtyp" -and $_.additionalProperties -contains "include_user_token"
})
if ($idtypClaim.Count -ne 1) {
    throw "The app registration must emit the idtyp claim for delegated access tokens."
}

Write-Host "Ensuring MISE image $targetImage is available in $AcrName..."
$imageExists = $true
& az acr repository show `
    --subscription $SubscriptionId `
    --name $AcrName `
    --image $targetImage `
    --only-show-errors `
    -o none 2>$null
if ($LASTEXITCODE -ne 0) {
    $imageExists = $false
}

if (-not $imageExists) {
    $requiredFeatures = @("ArtifactSyncPrivatePreview", "MicrosoftFirstParty")
    foreach ($feature in $requiredFeatures) {
        $state = & az feature show `
            --subscription $SubscriptionId `
            --namespace Microsoft.ContainerRegistry `
            --name $feature `
            --query properties.state `
            --only-show-errors `
            -o tsv
        if ($LASTEXITCODE -ne 0 -or $state -ne "Registered") {
            throw "Microsoft.ContainerRegistry/$feature must be Registered before MISE image sync (current state: $state)."
        }
    }
    & az provider register `
        --subscription $SubscriptionId `
        --namespace Microsoft.ContainerRegistry `
        --wait `
        --only-show-errors `
        -o none
    if ($LASTEXITCODE -ne 0) {
        throw "Microsoft.ContainerRegistry provider re-registration failed."
    }

    $acr = Invoke-AzJson acr show `
        --subscription $SubscriptionId `
        --name $AcrName
    $cacheRuleId = "$($acr.id)/cacheRules/$cacheRuleName"
    $ruleProperties = @{
        sourceRepository = $sourceRepository
        targetRepository = $targetRepository
        syncMode = "activeSync"
        syncReferrers = "enabled"
        artifactSyncFilters = @{
            tags = @{
                type = "kql"
                query = "Tags | where Name == '$MiseImageTag'"
            }
        }
    } | ConvertTo-Json -Depth 20 -Compress

    & az resource create `
        --id $cacheRuleId `
        --properties $ruleProperties `
        --latest-include-preview `
        --only-show-errors `
        -o none
    if ($LASTEXITCODE -ne 0) {
        throw "Creating the MISE artifact-sync rule failed."
    }
    & az resource wait `
        --updated `
        --ids $cacheRuleId `
        --interval 2 `
        --timeout 300 `
        --only-show-errors `
        -o none
    if ($LASTEXITCODE -ne 0) {
        throw "The MISE artifact-sync rule did not reach provisioningState=Succeeded."
    }
    $syncRequest = @{
        tagName = $MiseImageTag
        syncTagIfDeleted = $false
    } | ConvertTo-Json -Compress
    & az resource invoke-action `
        --ids $cacheRuleId `
        --action sync `
        --request-body $syncRequest `
        --latest-include-preview `
        --only-show-errors `
        -o none
    if ($LASTEXITCODE -ne 0) {
        throw "MISE artifact synchronization failed."
    }

    $imageDeadline = (Get-Date).AddMinutes(5)
    do {
        Start-Sleep -Seconds 10
        $syncedTag = & az acr repository show-tags `
            --subscription $SubscriptionId `
            --name $AcrName `
            --repository $targetRepository `
            --query "[?@=='$MiseImageTag'] | [0]" `
            --only-show-errors `
            -o tsv
    } while ($syncedTag -ne $MiseImageTag -and (Get-Date) -lt $imageDeadline)
    if ($syncedTag -ne $MiseImageTag) {
        throw "The MISE image did not appear in ACR within five minutes."
    }
}

$acr = Invoke-AzJson acr show `
    --subscription $SubscriptionId `
    --name $AcrName
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
if ($BackendImage) {
    $backend.image = $BackendImage
}
Set-ContainerEnvironmentVariable `
    -Container $backend `
    -Name "GENIE_MISE_ENDPOINT" `
    -Value "http://localhost:8080"

$miseImage = "$($acr.loginServer)/$targetImage"
$miseSidecar = [pscustomobject]@{
    name = "mise-sidecar"
    image = $miseImage
    imageType = "ContainerImage"
    resources = [pscustomobject]@{
        cpu = 0.5
        memory = "1Gi"
    }
    env = @(
        [pscustomobject]@{ name = "ASPNETCORE_HTTP_PORTS"; value = "8080" }
        [pscustomobject]@{ name = "MISE_CONTAINER_MiseVersion"; value = "2.0" }
        [pscustomobject]@{
            name = "MISE_CONTAINER_AzureAd__Instance"
            value = "https://login.microsoftonline.com/"
        }
        [pscustomobject]@{ name = "MISE_CONTAINER_AzureAd__TenantId"; value = $TenantId }
        [pscustomobject]@{ name = "MISE_CONTAINER_AzureAd__ClientId"; value = $ClientId }
        [pscustomobject]@{ name = "MISE_CONTAINER_AzureAd__Audiences__0"; value = $ClientId }
        [pscustomobject]@{
            name = "MISE_CONTAINER_AzureAd__Audiences__1"
            value = "api://$ClientId"
        }
        [pscustomobject]@{
            name = "MISE_CONTAINER_AzureAd__Protocols__Bearer__TokenTypes__AccessToken__AppToken"
            value = "true"
        }
        [pscustomobject]@{
            name = "MISE_CONTAINER_AzureAd__Protocols__Bearer__TokenTypes__AccessToken__UserToken"
            value = "true"
        }
    )
    probes = @(
        [pscustomobject]@{
            type = "Liveness"
            httpGet = [pscustomobject]@{ path = "/healthz"; port = 8080; scheme = "HTTP" }
            initialDelaySeconds = 10
            periodSeconds = 30
            timeoutSeconds = 5
            failureThreshold = 3
        }
        [pscustomobject]@{
            type = "Readiness"
            httpGet = [pscustomobject]@{ path = "/readyz"; port = 8080; scheme = "HTTP" }
            initialDelaySeconds = 5
            periodSeconds = 10
            timeoutSeconds = 5
            failureThreshold = 6
        }
    )
}

$otherContainers = @($app.properties.template.containers | Where-Object {
    $_.name -ne $BackendContainerName -and $_.name -ne "mise-sidecar"
})
$containers = @($backend) + $otherContainers + @($miseSidecar)
$patch = @{
    properties = @{
        template = @{
            containers = $containers
        }
    }
}

$patchFile = Join-Path $env:TEMP "genie-mise-$([guid]::NewGuid().ToString('N')).json"
try {
    $patch | ConvertTo-Json -Depth 100 | Set-Content -Path $patchFile -Encoding utf8
    & az rest `
        --method patch `
        --uri "$($app.id)?api-version=2025-01-01" `
        --body "@$patchFile" `
        --only-show-errors `
        -o none
    if ($LASTEXITCODE -ne 0) {
        throw "Container App update failed."
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
        $current.properties.runningStatus -eq "Running"
    )
} while (-not $ready -and (Get-Date) -lt $deadline)

if (-not $ready) {
    throw "The updated Container App did not become ready within $WaitTimeoutSeconds seconds."
}

$deployedSidecar = @($current.properties.template.containers | Where-Object {
    $_.name -eq "mise-sidecar"
})
if ($deployedSidecar.Count -ne 1 -or $deployedSidecar[0].image -ne $miseImage) {
    throw "MISE sidecar verification failed after deployment."
}

$healthUri = "https://$($current.properties.configuration.ingress.fqdn)/health/ready"
$health = Invoke-RestMethod -Method Get -Uri $healthUri -TimeoutSec 30
if ($health.status -ne "ready") {
    throw "Backend readiness verification failed."
}

[pscustomobject]@{
    containerApp = $current.name
    revision = $current.properties.latestReadyRevisionName
    runningStatus = $current.properties.runningStatus
    backendImage = $backend.image
    miseImage = $miseImage
    clientId = $ClientId
    tenantId = $TenantId
    health = $health.status
} | ConvertTo-Json -Depth 10
