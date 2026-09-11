param location string
param name string
param addressPrefix string
param containerAppsInfrastructureSubnetPrefix string
param privateEndpointSubnetPrefix string
param apiManagementSubnetPrefix string
param tags object

resource apiManagementNetworkSecurityGroup 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: '${name}-apim-nsg'
  location: location
  tags: tags
  properties: {
    securityRules: []
  }
}

resource virtualNetwork 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        addressPrefix
      ]
    }
    subnets: [
      {
        name: 'container-apps-infrastructure'
        properties: {
          addressPrefix: containerAppsInfrastructureSubnetPrefix
          delegations: [
            {
              name: 'container-apps-environment'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
      {
        name: 'private-endpoints'
        properties: {
          addressPrefix: privateEndpointSubnetPrefix
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
      {
        name: 'api-management'
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
    ]
  }
}

output id string = virtualNetwork.id
output containerAppsInfrastructureSubnetId string = virtualNetwork.properties.subnets[0].id
output privateEndpointSubnetId string = virtualNetwork.properties.subnets[1].id
output apiManagementSubnetId string = virtualNetwork.properties.subnets[2].id