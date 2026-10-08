param location string
param name string
param addressPrefix string
param containerAppsInfrastructureSubnetPrefix string
param privateEndpointSubnetPrefix string
param apiManagementSubnetPrefix string
param acrAgentPoolSubnetPrefix string
param tags object

resource apiManagementNetworkSecurityGroup 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: '${name}-apim-nsg'
  location: location
  tags: tags
  properties: {
    securityRules: []
  }
}

// The private-endpoints subnet fronts the Container Apps environment's
// private endpoint - the ONLY network path a backend request can take once
// the environment's public network access is disabled. Without this NSG,
// Azure's default AllowVnetInBound rule lets anything reachable as
// "VirtualNetwork" (including VPN/ExpressRoute-connected corporate
// networks that can resolve the environment's privatelink DNS zone) reach
// the backend directly, completely bypassing the platform APIM's CORS,
// rate-limit, and correlation-ID policy. These rules make APIM's dedicated
// VNet-integration subnet the only allowed source.
resource privateEndpointNetworkSecurityGroup 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: '${name}-private-endpoints-nsg'
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        name: 'AllowApiManagementSubnetHttps'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: apiManagementSubnetPrefix
          sourcePortRange: '*'
          destinationAddressPrefix: privateEndpointSubnetPrefix
          destinationPortRange: '443'
          description: 'Allow only the platform APIM VNet-integration subnet to reach the backend private endpoint.'
        }
      }
      {
        name: 'DenyAllOtherInbound'
        properties: {
          priority: 200
          direction: 'Inbound'
          access: 'Deny'
          protocol: '*'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: privateEndpointSubnetPrefix
          destinationPortRange: '*'
          description: 'Deny all else so only APIM can reach the backend via this private endpoint.'
        }
      }
    ]
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
          networkSecurityGroup: {
            id: privateEndpointNetworkSecurityGroup.id
          }
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
      {
        name: 'acr-agent-pool'
        properties: {
          addressPrefix: acrAgentPoolSubnetPrefix
          serviceEndpoints: [
            {
              service: 'Microsoft.ContainerRegistry'
            }
            {
              service: 'Microsoft.Storage'
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
output acrAgentPoolSubnetId string = virtualNetwork.properties.subnets[3].id