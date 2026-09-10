targetScope = 'subscription'

@description('Principal id of the Genie backend managed identity that provisions prototype gateways.')
param managedIdentityPrincipalId string

var apiManagementServiceContributorRoleId = '312a565d-c81f-4fd8-895a-4e21e48d571c'
var networkContributorRoleId = '4d97b98b-1d4f-4787-a291-c67834d212e7'

resource apiManagementServiceContributorRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().id, managedIdentityPrincipalId, apiManagementServiceContributorRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      apiManagementServiceContributorRoleId
    )
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource networkContributorRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().id, managedIdentityPrincipalId, networkContributorRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      networkContributorRoleId
    )
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}