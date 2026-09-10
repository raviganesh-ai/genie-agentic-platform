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

output managedIdentityPrincipalId string = managedIdentity.outputs.principalId
output managedIdentityClientId string = managedIdentity.outputs.clientId
output keyVaultUri string = keyVault.outputs.vaultUri
output storageAccountName string = storageAccount.outputs.name
output aiSearchEndpoint string = aiSearch.outputs.endpoint
output cosmosDbEndpoint string = cosmosDb.outputs.endpoint
output aiFoundryEndpoint string = aiFoundry.outputs.endpoint
output containerAppsEnvironmentId string = containerAppsEnvironment.outputs.id
output staticWebAppDefaultHostname string = staticWebApp.outputs.defaultHostname
output applicationInsightsConnectionString string = appInsights.outputs.connectionString
output logAnalyticsWorkspaceId string = logAnalytics.outputs.workspaceId
