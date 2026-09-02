<#
.SYNOPSIS
    Sends authenticated requests through the deployed Genie MISE gateway.

.DESCRIPTION
    Acquires a short-lived token for the configured API with the current Azure
    CLI identity, sends traceable requests, and emits non-secret evidence. The
    access token is never written to output or disk.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^https://')]
    [string]$ApiBaseUrl,

    [Parameter(Mandatory = $true)]
    [string]$ClientId,

    [SecureString]$AccessToken,

    [ValidateRange(1, 20)]
    [int]$RequestCount = 5
)

$ErrorActionPreference = "Stop"
$tokenText = $null
$secureToken = $AccessToken
$evidence = @()

try {
    if ($null -eq $secureToken) {
        $tokenText = & az account get-access-token `
            --resource "api://$ClientId" `
            --query accessToken `
            --only-show-errors `
            -o tsv
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($tokenText)) {
            throw "Supply -AccessToken as a SecureString or use an Azure CLI identity preauthorized for the Genie API."
        }
        $secureToken = ConvertTo-SecureString $tokenText -AsPlainText -Force
        $tokenText = $null
    }

    $sessionsUri = "$($ApiBaseUrl.TrimEnd('/'))/sessions"
    foreach ($requestNumber in 1..$RequestCount) {
        $correlationId = "mise-sfi-$([guid]::NewGuid().ToString('N'))"
        $timestampUtc = (Get-Date).ToUniversalTime().ToString("o")
        try {
            $response = Invoke-WebRequest `
                -Method Get `
                -Uri $sessionsUri `
                -Authentication Bearer `
                -Token $secureToken `
                -Headers @{ "X-Correlation-Id" = $correlationId } `
                -TimeoutSec 60
            $statusCode = [int]$response.StatusCode
        }
        catch {
            $statusCode = [int]$_.Exception.Response.StatusCode
        }

        $evidence += [pscustomobject]@{
            requestNumber = $requestNumber
            timestampUtc = $timestampUtc
            correlationId = $correlationId
            statusCode = $statusCode
        }
    }
}
finally {
    $tokenText = $null
    $secureToken = $null
}

$failed = @($evidence | Where-Object { $_.statusCode -lt 200 -or $_.statusCode -ge 300 })
$evidence | ConvertTo-Json -Depth 5
if ($failed.Count -ne 0) {
    throw "$($failed.Count) authenticated gateway request(s) failed."
}