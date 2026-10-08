// Azure Container Registry: hosts the commit-pinned FastAPI backend image,
// per the "Images and hosting" Azure concern in README.md. Grants the
// shared managed identity pull-only access (never push/delete) so the
// running backend can resolve its own image without broader registry
// control - image builds/pushes are performed by a human or CI pipeline
// using their own `az acr build`/`az acr login` session, never the
// runtime identity.
param location string
param name string
param managedIdentityPrincipalId string
param agentPoolSubnetId string

@description('Azure Container Registry SKU. "Basic" is sufficient for a single evaluation environment; use "Standard" or "Premium" for higher throughput/geo-replication.')
@allowed([
  'Basic'
  'Standard'
  'Premium'
])
param skuName string = 'Basic'

param tags object

// Built-in role definition id for "AcrPull" - read-only image pull, no
// push/delete/registry-management permission.
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: skuName
  }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Disabled'
    networkRuleBypassOptions: 'AzureServices'
    networkRuleSet: {
      defaultAction: 'Deny'
    }
  }
}

resource buildAgentPool 'Microsoft.ContainerRegistry/registries/agentPools@2025-03-01-preview' = {
  parent: containerRegistry
  name: 'genie-build-pool'
  location: location
  tags: tags
  properties: {
    count: 1
    os: 'Linux'
    tier: 'S1'
    virtualNetworkSubnetResourceId: agentPoolSubnetId
  }
}

resource acrPullRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(containerRegistry.id, managedIdentityPrincipalId, acrPullRoleId)
  scope: containerRegistry
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      acrPullRoleId
    )
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

output name string = containerRegistry.name
output loginServer string = containerRegistry.properties.loginServer
output buildAgentPoolName string = buildAgentPool.name
