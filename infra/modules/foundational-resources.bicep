// Resource-group-scoped aggregator: provisions every foundational Azure
// resource Genie needs and wires least-privilege RBAC from the shared
// managed identity to each of them. Invoked once, at resource-group scope,
// by infra/main.bicep.
param location string
param aiSearchLocation string = location
param resourcePrefix string
param resourceToken string
param virtualNetworkAddressPrefix string
param containerAppsInfrastructureSubnetPrefix string
param privateEndpointSubnetPrefix string
param apiManagementSubnetPrefix string
param acrAgentPoolSubnetPrefix string
@description('Model deployments to create on the Foundry account - see ai-foundry.bicep.')
param foundryModelDeployments array = []
@description('Deploy the Azure Container Registry and a bootstrap backend Container App as part of this same template (recommended for a first deployment into an empty subscription). Set to false to reuse an existing registry/app instead.')
param deployContainerRegistryAndBackendApp bool = true
@allowed([
  'Basic'
  'Standard'
  'Premium'
])
param containerRegistrySkuName string = 'Premium'
param tags object

// Built-in role definition ids - granted to Genie's own runtime managed
// identity at THIS resource group's scope (not a single sub-resource like
// Key Vault/Storage below) because Deploy & Launch's real per-mission RBAC
// step (app.deploy_launch.mission_identity_service) needs to create a
// brand-new Azure managed identity per deployed mission and assign it
// real, least-privilege roles - both are resource-group-level
// capabilities, not scoped to any one resource. Deliberately NOT
// subscription-scoped Owner/Contributor.
var managedIdentityContributorRoleId = 'e40ec5ca-96e0-45a2-b4ff-59039f2c2b59'
var userAccessAdministratorRoleId = '18d7d88d-d35e-4fb5-a5c3-7773c20a72d9'
var managedIdentityName = '${resourcePrefix}-${resourceToken}-identity'

module logAnalytics 'log-analytics.bicep' = {
  name: 'genie-log-analytics'
  params: {
    location: location
    name: '${resourcePrefix}-${resourceToken}-log'
    tags: tags
  }
}

module virtualNetwork 'virtual-network.bicep' = {
  name: 'genie-virtual-network'
  params: {
    location: location
    name: '${resourcePrefix}-${resourceToken}-vnet'
    addressPrefix: virtualNetworkAddressPrefix
    containerAppsInfrastructureSubnetPrefix: containerAppsInfrastructureSubnetPrefix
    privateEndpointSubnetPrefix: privateEndpointSubnetPrefix
    apiManagementSubnetPrefix: apiManagementSubnetPrefix
    acrAgentPoolSubnetPrefix: acrAgentPoolSubnetPrefix
    tags: tags
  }
}

module appInsights 'app-insights.bicep' = {
  name: 'genie-app-insights'
  params: {
    location: location
    name: '${resourcePrefix}-${resourceToken}-appi'
    logAnalyticsWorkspaceId: logAnalytics.outputs.workspaceId
    tags: tags
  }
}

module managedIdentity 'managed-identity.bicep' = {
  name: 'genie-managed-identity'
  params: {
    location: location
    name: managedIdentityName
    tags: tags
  }
}

resource managedIdentityContributorRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, managedIdentityName, managedIdentityContributorRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      managedIdentityContributorRoleId
    )
    principalId: managedIdentity.outputs.principalId
    principalType: 'ServicePrincipal'
  }
}

resource userAccessAdministratorRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, managedIdentityName, userAccessAdministratorRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      userAccessAdministratorRoleId
    )
    principalId: managedIdentity.outputs.principalId
    principalType: 'ServicePrincipal'
  }
}

module keyVault 'key-vault.bicep' = {
  name: 'genie-key-vault'
  params: {
    location: location
    // Key Vault names must be <= 24 chars and globally unique.
    name: take('${resourcePrefix}kv${resourceToken}', 24)
    managedIdentityPrincipalId: managedIdentity.outputs.principalId
    tags: tags
  }
}

module storageAccount 'storage-account.bicep' = {
  name: 'genie-storage-account'
  params: {
    location: location
    // Storage account names must be <= 24 chars, lowercase letters/numbers only.
    name: take(toLower('${resourcePrefix}st${resourceToken}'), 24)
    managedIdentityPrincipalId: managedIdentity.outputs.principalId
    tags: tags
  }
}

module aiSearch 'ai-search.bicep' = {
  name: 'genie-ai-search'
  params: {
    location: aiSearchLocation
    name: '${resourcePrefix}-${resourceToken}-search'
    managedIdentityPrincipalId: managedIdentity.outputs.principalId
    tags: tags
  }
}

module aiFoundry 'ai-foundry.bicep' = {
  name: 'genie-ai-foundry'
  params: {
    location: location
    accountName: '${resourcePrefix}-${resourceToken}-foundry'
    projectName: '${resourcePrefix}-${resourceToken}-project'
    managedIdentityPrincipalId: managedIdentity.outputs.principalId
    modelDeployments: foundryModelDeployments
    tags: tags
  }
}

module containerAppsEnvironment 'container-apps-environment.bicep' = {
  name: 'genie-container-apps-env'
  params: {
    location: location
    name: '${resourcePrefix}-${resourceToken}-cae'
    logAnalyticsWorkspaceId: logAnalytics.outputs.workspaceId
    infrastructureSubnetId: virtualNetwork.outputs.containerAppsInfrastructureSubnetId
    tags: tags
  }
}

module cosmosDb 'cosmos-db.bicep' = {
  name: 'genie-cosmos-db'
  params: {
    location: location
    name: '${resourcePrefix}-${resourceToken}-cosmos'
    managedIdentityPrincipalId: managedIdentity.outputs.principalId
    virtualNetworkId: virtualNetwork.outputs.id
    privateEndpointSubnetId: virtualNetwork.outputs.privateEndpointSubnetId
    tags: tags
  }
}

module staticWebApp 'static-web-app.bicep' = {
  name: 'genie-static-web-app'
  params: {
    // Static Web Apps are only available in a subset of regions; the
    // caller-supplied location is used as-is and validated by Azure at
    // deployment time rather than hardcoded to a specific region here.
    location: location
    name: '${resourcePrefix}-${resourceToken}-swa'
    tags: tags
  }
}

module containerRegistry 'container-registry.bicep' = if (deployContainerRegistryAndBackendApp) {
  name: 'genie-container-registry'
  params: {
    location: location
    // ACR names must be globally unique, 5-50 chars, alphanumeric only.
    name: take(toLower('${resourcePrefix}acr${resourceToken}'), 50)
    managedIdentityPrincipalId: managedIdentity.outputs.principalId
    skuName: containerRegistrySkuName
    agentPoolSubnetId: virtualNetwork.outputs.acrAgentPoolSubnetId
    tags: tags
  }
}

module backendContainerApp 'backend-container-app.bicep' = if (deployContainerRegistryAndBackendApp) {
  name: 'genie-backend-container-app'
  params: {
    location: location
    name: '${resourcePrefix}-${resourceToken}-backend'
    containerAppsEnvironmentId: containerAppsEnvironment.outputs.id
    // Non-null assertion (not #disable-next-line, which only applies to
    // linter rules, not this BCP318 compiler diagnostic): both modules
    // share the exact same `deployContainerRegistryAndBackendApp` guard,
    // so containerRegistry is always created whenever this module is.
    containerRegistryLoginServer: deployContainerRegistryAndBackendApp ? containerRegistry!.outputs.loginServer : ''
    managedIdentityResourceId: managedIdentity.outputs.resourceId
    managedIdentityClientId: managedIdentity.outputs.clientId
    tags: tags
  }
}

output managedIdentityPrincipalId string = managedIdentity.outputs.principalId
output managedIdentityClientId string = managedIdentity.outputs.clientId
output managedIdentityResourceId string = managedIdentity.outputs.resourceId
output keyVaultUri string = keyVault.outputs.vaultUri
output keyVaultName string = keyVault.outputs.vaultName
output storageAccountName string = storageAccount.outputs.name
output aiSearchEndpoint string = aiSearch.outputs.endpoint
output cosmosDbEndpoint string = cosmosDb.outputs.endpoint
output aiFoundryEndpoint string = aiFoundry.outputs.endpoint
output aiFoundryAccountName string = aiFoundry.outputs.accountName
output aiFoundryProjectName string = aiFoundry.outputs.projectName
output containerAppsEnvironmentId string = containerAppsEnvironment.outputs.id
output containerAppsEnvironmentName string = containerAppsEnvironment.outputs.name
output staticWebAppDefaultHostname string = staticWebApp.outputs.defaultHostname
output staticWebAppName string = staticWebApp.outputs.name
output applicationInsightsConnectionString string = appInsights.outputs.connectionString
output logAnalyticsWorkspaceId string = logAnalytics.outputs.workspaceId
// Non-null assertion: each output expression re-checks the exact same
// `deployContainerRegistryAndBackendApp` guard the module was created under.
output containerRegistryName string = deployContainerRegistryAndBackendApp ? containerRegistry!.outputs.name : ''
output containerRegistryLoginServer string = deployContainerRegistryAndBackendApp ? containerRegistry!.outputs.loginServer : ''
output backendContainerAppName string = deployContainerRegistryAndBackendApp ? backendContainerApp!.outputs.name : ''
output backendContainerAppFqdn string = deployContainerRegistryAndBackendApp ? backendContainerApp!.outputs.fqdn : ''


