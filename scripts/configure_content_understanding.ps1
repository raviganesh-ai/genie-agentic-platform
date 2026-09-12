<#
.SYNOPSIS
    Configures Azure Content Understanding model deployments and defaults.

.DESCRIPTION
    Idempotently ensures the externally specified completion and embedding
    deployments exist on Genie's AIServices account, validates both model
    families against the selected analyzer, applies its model aliases as
    resource defaults, and verifies the saved mappings. No keys are used or
    printed; authentication uses the caller's Microsoft Entra identity.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SubscriptionId,

    [Parameter(Mandatory = $true)]
    [string]$ResourceGroup,

    [Parameter(Mandatory = $true)]
    [string]$AccountName,

    [Parameter(Mandatory = $true)]
    [string]$CompletionDeploymentName,

    [Parameter(Mandatory = $true)]
    [string]$CompletionModelName,

    [Parameter(Mandatory = $true)]
    [string]$CompletionModelVersion,

    [Parameter(Mandatory = $true)]
    [string]$EmbeddingDeploymentName,

    [Parameter(Mandatory = $true)]
    [string]$EmbeddingModelName,

    [Parameter(Mandatory = $true)]
    [string]$EmbeddingModelVersion,

    [string]$CompletionSkuName = "GlobalStandard",
    [int]$CompletionCapacity = 100,
    [string]$EmbeddingSkuName = "GlobalStandard",
    [int]$EmbeddingCapacity = 120,
    [string]$AnalyzerId = "prebuilt-documentSearch",
    [ValidateSet("geography", "dataZone", "global")]
    [string]$ProcessingLocation = "geography",
    [string]$ApiVersion = "2025-11-01"
)

$ErrorActionPreference = "Stop"

function Assert-LastExitCode {
    param([Parameter(Mandatory = $true)][string]$Operation)

    if ($LASTEXITCODE -ne 0) {
        throw "$Operation failed with exit code $LASTEXITCODE."
    }
}

function Get-Deployment {
    param([Parameter(Mandatory = $true)][string]$DeploymentName)

    $json = & az cognitiveservices account deployment show `
        --subscription $SubscriptionId `
        --resource-group $ResourceGroup `
        --name $AccountName `
        --deployment-name $DeploymentName `
        --only-show-errors `
        -o json 2>$null
    if ($LASTEXITCODE -ne 0) {
        return $null
    }
    return $json | ConvertFrom-Json
}

function Ensure-Deployment {
    param(
        [Parameter(Mandatory = $true)][string]$DeploymentName,
        [Parameter(Mandatory = $true)][string]$ModelName,
        [Parameter(Mandatory = $true)][string]$ModelVersion,
        [Parameter(Mandatory = $true)][string]$SkuName,
        [Parameter(Mandatory = $true)][int]$Capacity
    )

    $deployment = Get-Deployment -DeploymentName $DeploymentName
    if ($null -eq $deployment) {
        & az cognitiveservices account deployment create `
            --subscription $SubscriptionId `
            --resource-group $ResourceGroup `
            --name $AccountName `
            --deployment-name $DeploymentName `
            --model-format OpenAI `
            --model-name $ModelName `
            --model-version $ModelVersion `
            --sku-name $SkuName `
            --sku-capacity $Capacity `
            --only-show-errors `
            -o none
        Assert-LastExitCode -Operation "Creating model deployment '$DeploymentName'"
        $deployment = Get-Deployment -DeploymentName $DeploymentName
    }

    if (
        $deployment.properties.model.name -ne $ModelName -or
        $deployment.properties.model.version -ne $ModelVersion
    ) {
        throw "Deployment '$DeploymentName' does not match model '$ModelName' version '$ModelVersion'."
    }
    return $deployment
}

$accountJson = & az cognitiveservices account show `
    --subscription $SubscriptionId `
    --resource-group $ResourceGroup `
    --name $AccountName `
    --only-show-errors `
    -o json
Assert-LastExitCode -Operation "Reading AIServices account '$AccountName'"
$account = $accountJson | ConvertFrom-Json
if ($account.kind -ne "AIServices") {
    throw "Account '$AccountName' must have kind AIServices, not '$($account.kind)'."
}

$completion = Ensure-Deployment `
    -DeploymentName $CompletionDeploymentName `
    -ModelName $CompletionModelName `
    -ModelVersion $CompletionModelVersion `
    -SkuName $CompletionSkuName `
    -Capacity $CompletionCapacity
$embedding = Ensure-Deployment `
    -DeploymentName $EmbeddingDeploymentName `
    -ModelName $EmbeddingModelName `
    -ModelVersion $EmbeddingModelVersion `
    -SkuName $EmbeddingSkuName `
    -Capacity $EmbeddingCapacity

$token = & az account get-access-token `
    --subscription $SubscriptionId `
    --resource https://cognitiveservices.azure.com `
    --query accessToken `
    --output tsv
Assert-LastExitCode -Operation "Acquiring a Cognitive Services access token"
$headers = @{ Authorization = "Bearer $token" }
$endpoint = ([string]$account.properties.endpoint).TrimEnd("/")
$analyzerUri = "$endpoint/contentunderstanding/analyzers/$($AnalyzerId)?api-version=$ApiVersion"
$analyzer = Invoke-RestMethod -Method Get -Uri $analyzerUri -Headers $headers -TimeoutSec 60
if ($analyzer.status -ne "ready") {
    throw "Content Understanding analyzer '$AnalyzerId' is not ready."
}
if ($analyzer.supportedModels.completion -notcontains $completion.properties.model.name) {
    throw "Completion model '$($completion.properties.model.name)' is not supported by '$AnalyzerId'."
}
if ($analyzer.supportedModels.embedding -notcontains $embedding.properties.model.name) {
    throw "Embedding model '$($embedding.properties.model.name)' is not supported by '$AnalyzerId'."
}

$modelDeployments = @{}
$modelDeployments[$analyzer.models.completion] = $CompletionDeploymentName
$modelDeployments[$analyzer.models.embedding] = $EmbeddingDeploymentName
$body = @{ modelDeployments = $modelDeployments } | ConvertTo-Json -Depth 5
$defaultsUri = "$endpoint/contentunderstanding/defaults?api-version=$ApiVersion"
Invoke-RestMethod `
    -Method Patch `
    -Uri $defaultsUri `
    -Headers $headers `
    -ContentType "application/json" `
    -Body $body `
    -TimeoutSec 60 | Out-Null

$saved = Invoke-RestMethod -Method Get -Uri $defaultsUri -Headers $headers -TimeoutSec 60
foreach ($alias in $modelDeployments.Keys) {
    if ($saved.modelDeployments.$alias -ne $modelDeployments[$alias]) {
        throw "Content Understanding default '$alias' was not saved correctly."
    }
}

[pscustomobject]@{
    accountName = $AccountName
    endpoint = $endpoint
    analyzerId = $AnalyzerId
    analyzerStatus = $analyzer.status
    processingLocation = $ProcessingLocation
    modelDeployments = $saved.modelDeployments
} | ConvertTo-Json -Depth 8
