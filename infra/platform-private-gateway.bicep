targetScope = 'resourceGroup'

@description('Azure region containing the existing Genie networking and Container Apps resources.')
param location string = resourceGroup().location

@description('Existing Genie Container App name published through API Management.')
param containerAppName string

@description('Existing Container Apps managed environment name.')
param containerAppsEnvironmentName string

@description('Existing Genie virtual network name.')
param virtualNetworkName string

@description('Existing subnet used for private endpoints.')
param privateEndpointSubnetName string = 'private-endpoints'

@description('Dedicated subnet used by API Management for outbound VNet integration.')
param apiManagementSubnetName string = 'api-management'

@description('Address prefix reserved for the dedicated API Management subnet.')
param apiManagementSubnetPrefix string = '10.20.4.0/24'

@description('API Management publisher contact email.')
param publisherEmail string

@description('API Management publisher display name.')
param publisherName string

@description('Exact public Static Web Apps origin allowed by the API policy.')
param allowedOrigin string

@description('Creates the Container Apps private endpoint and private DNS after public access is disabled.')
param enablePrivateEndpoint bool = false

@description('Tags applied to the platform gateway resources.')
param tags object = {
  application: 'genie'
  component: 'platform-api-gateway'
}

var apiManagementName = take('${containerAppName}-${uniqueString(subscription().id, resourceGroup().id, containerAppName)}-apim', 50)
var privateDnsZoneName = 'privatelink.${location}.azurecontainerapps.io'
var proxyMethods = [
  'GET'
  'POST'
  'PUT'
  'PATCH'
  'DELETE'
  'HEAD'
]

resource virtualNetwork 'Microsoft.Network/virtualNetworks@2024-05-01' existing = {
  name: virtualNetworkName
}

resource privateEndpointSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' existing = {
  parent: virtualNetwork
  name: privateEndpointSubnetName
}

resource apiManagementNetworkSecurityGroup 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: '${virtualNetworkName}-apim-nsg'
  location: location
  tags: tags
  properties: {
    securityRules: []
  }
}

resource apiManagementSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' = {
  parent: virtualNetwork
  name: apiManagementSubnetName
  properties: {
    addressPrefix: apiManagementSubnetPrefix
    networkSecurityGroup: {
      id: apiManagementNetworkSecurityGroup.id
    }
    delegations: [
      {
        name: 'api-management-integration'
        properties: {
          serviceName: 'Microsoft.Web/serverFarms'
        }
      }
    ]
  }
}

resource containerAppsEnvironment 'Microsoft.App/managedEnvironments@2025-01-01' existing = {
  name: containerAppsEnvironmentName
}

resource containerApp 'Microsoft.App/containerApps@2025-01-01' existing = {
  name: containerAppName
}

resource apiManagement 'Microsoft.ApiManagement/service@2024-05-01' = {
  name: apiManagementName
  location: location
  tags: tags
  sku: {
    name: 'StandardV2'
    capacity: 1
  }
  properties: {
    publisherEmail: publisherEmail
    publisherName: publisherName
    publicNetworkAccess: 'Enabled'
    virtualNetworkType: 'External'
    virtualNetworkConfiguration: {
      subnetResourceId: apiManagementSubnet.id
    }
    configurationApi: {
      legacyApi: 'Disabled'
    }
    developerPortalStatus: 'Disabled'
    legacyPortalStatus: 'Disabled'
  }
}

resource genieApi 'Microsoft.ApiManagement/service/apis@2024-05-01' = {
  parent: apiManagement
  name: 'genie'
  properties: {
    displayName: 'Genie API'
    path: ''
    protocols: [
      'https'
    ]
    serviceUrl: 'https://${containerApp.properties.configuration.ingress.fqdn}'
    subscriptionRequired: false
  }
}

resource proxyOperations 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = [for method in proxyMethods: {
  parent: genieApi
  name: 'proxy-${toLower(method)}'
  properties: {
    displayName: '${method} proxy'
    method: method
    urlTemplate: '/*'
  }
}]

resource genieApiPolicy 'Microsoft.ApiManagement/service/apis/policies@2024-05-01' = {
  parent: genieApi
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: replace(
      loadTextContent('policies/genie-api-policy.xml'),
      '__ALLOWED_ORIGIN__',
      allowedOrigin
    )
  }
}

resource privateDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = if (enablePrivateEndpoint) {
  name: privateDnsZoneName
  location: 'global'
  tags: tags
}

resource privateDnsZoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = if (enablePrivateEndpoint) {
  parent: privateDnsZone
  name: 'genie-platform-vnet-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: virtualNetwork.id
    }
  }
}

resource containerAppsPrivateEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = if (enablePrivateEndpoint) {
  name: '${containerAppsEnvironmentName}-private-endpoint'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: privateEndpointSubnet.id
    }
    privateLinkServiceConnections: [
      {
        name: '${containerAppsEnvironmentName}-private-link'
        properties: {
          privateLinkServiceId: containerAppsEnvironment.id
          groupIds: [
            'managedEnvironments'
          ]
        }
      }
    ]
  }
}

resource containerAppsPrivateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = if (enablePrivateEndpoint) {
  parent: containerAppsPrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'container-apps-environment'
        properties: {
          privateDnsZoneId: privateDnsZone.id
        }
      }
    ]
  }
}

output apiManagementName string = apiManagement.name
output gatewayUrl string = 'https://${apiManagementName}.azure-api.net'
output backendFqdn string = containerApp.properties.configuration.ingress.fqdn
output privateEndpointEnabled bool = enablePrivateEndpoint