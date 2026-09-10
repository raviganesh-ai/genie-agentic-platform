targetScope = 'resourceGroup'

param location string = resourceGroup().location
param virtualNetworkName string
param containerAppsEnvironmentName string
param cosmosAccountName string
param logAnalyticsWorkspaceName string
param virtualNetworkAddressPrefix string = '10.20.0.0/16'
param containerAppsInfrastructureSubnetPrefix string = '10.20.0.0/23'
param privateEndpointSubnetPrefix string = '10.20.2.0/24'
param tags object = {
  application: 'genie'
  migration: 'corporate-private-network'
}

resource logAnalyticsWorkspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: logAnalyticsWorkspaceName
}

resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' existing = {
  name: cosmosAccountName
}

module virtualNetwork '../modules/virtual-network.bicep' = {
  name: 'genie-corporate-virtual-network'
  params: {
    location: location
    name: virtualNetworkName
    addressPrefix: virtualNetworkAddressPrefix
    containerAppsInfrastructureSubnetPrefix: containerAppsInfrastructureSubnetPrefix
    privateEndpointSubnetPrefix: privateEndpointSubnetPrefix
    tags: tags
  }
}

module containerAppsEnvironment '../modules/container-apps-environment.bicep' = {
  name: 'genie-corporate-container-apps-environment'
  params: {
    location: location
    name: containerAppsEnvironmentName
    logAnalyticsWorkspaceId: logAnalyticsWorkspace.id
    infrastructureSubnetId: virtualNetwork.outputs.containerAppsInfrastructureSubnetId
    tags: tags
  }
}

module cosmosPrivateEndpoint '../modules/cosmos-private-endpoint.bicep' = {
  name: 'genie-corporate-cosmos-private-endpoint'
  params: {
    location: location
    cosmosAccountId: cosmosAccount.id
    cosmosAccountName: cosmosAccount.name
    virtualNetworkId: virtualNetwork.outputs.id
    privateEndpointSubnetId: virtualNetwork.outputs.privateEndpointSubnetId
    tags: tags
  }
}

output containerAppsEnvironmentId string = containerAppsEnvironment.outputs.id
output containerAppsEnvironmentDefaultDomain string = containerAppsEnvironment.outputs.defaultDomain
output cosmosPrivateEndpointId string = cosmosPrivateEndpoint.outputs.privateEndpointId