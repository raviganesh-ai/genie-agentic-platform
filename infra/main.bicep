// Genie foundational infrastructure - subscription-scoped entry point.
//
// Assumes the target Azure subscription is EMPTY: creates its own resource
// group and every foundational resource Genie depends on (no existing
// resource group, managed identity, Key Vault, AI Search, Cosmos DB,
// Storage Account, Container Apps environment, Static Web App, Application
// Insights, or Log Analytics workspace is assumed to exist).
//
// Run `scripts/validate_deployment_readiness.py` (or
// `scripts/deploy_infra.ps1`, which runs it automatically) BEFORE this
// template - deployment readiness validation must occur before
// infrastructure provisioning, per .github/copilot-instructions.md.
//
// Usage:
//   az deployment sub create \
//     --location <location> \
//     --template-file infra/main.bicep \
//     --parameters infra/main.parameters.json
targetScope = 'subscription'

@minLength(1)
@maxLength(16)
@description('Short, unique name for this Genie environment (e.g. "dev", "prod"). Used to derive resource names.')
param environmentName string

@description('Azure region every resource is deployed into.')
param location string

@description('Azure region for Azure AI Search. Defaults to `location`; override this independently if AI Search lacks capacity in the primary region.')
param aiSearchLocation string = location

@description('Address space reserved for the Genie Container Apps environment and private endpoints.')
param virtualNetworkAddressPrefix string = '10.20.0.0/16'

@description('Dedicated subnet for the Container Apps workload-profiles environment; must be /27 or larger.')
param containerAppsInfrastructureSubnetPrefix string = '10.20.0.0/23'

@description('Dedicated subnet for Azure private endpoints.')
param privateEndpointSubnetPrefix string = '10.20.2.0/24'

@description('Short prefix applied to every resource name (lowercase letters/numbers only).')
@minLength(2)
@maxLength(8)
param resourcePrefix string = 'genie'

// Deterministic, collision-resistant suffix derived from the subscription
// and environment name - never a hardcoded/customer-specific value.
var resourceToken = uniqueString(subscription().id, environmentName, location)
var resourceGroupName = '${resourcePrefix}-${environmentName}-rg'
var tags = {
  application: 'genie'
  environment: environmentName
}

resource resourceGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

module foundationalResources 'modules/foundational-resources.bicep' = {
  name: 'genie-foundational-resources'
  scope: resourceGroup
  params: {
    location: location
    aiSearchLocation: aiSearchLocation
    resourcePrefix: resourcePrefix
    resourceToken: resourceToken
    virtualNetworkAddressPrefix: virtualNetworkAddressPrefix
    containerAppsInfrastructureSubnetPrefix: containerAppsInfrastructureSubnetPrefix
    privateEndpointSubnetPrefix: privateEndpointSubnetPrefix
    tags: tags
  }
}

output resourceGroupName string = resourceGroup.name
output managedIdentityPrincipalId string = foundationalResources.outputs.managedIdentityPrincipalId
output keyVaultUri string = foundationalResources.outputs.keyVaultUri
output storageAccountName string = foundationalResources.outputs.storageAccountName
output aiSearchEndpoint string = foundationalResources.outputs.aiSearchEndpoint
output cosmosDbEndpoint string = foundationalResources.outputs.cosmosDbEndpoint
output aiFoundryEndpoint string = foundationalResources.outputs.aiFoundryEndpoint
output containerAppsEnvironmentId string = foundationalResources.outputs.containerAppsEnvironmentId
output staticWebAppDefaultHostname string = foundationalResources.outputs.staticWebAppDefaultHostname
output applicationInsightsConnectionString string = foundationalResources.outputs.applicationInsightsConnectionString
output logAnalyticsWorkspaceId string = foundationalResources.outputs.logAnalyticsWorkspaceId
