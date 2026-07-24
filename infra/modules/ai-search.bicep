// Azure AI Search: backs the Enterprise Knowledge Memory tier per the
// Memory Architecture in .github/copilot-instructions.md.
param location string
param name string
param managedIdentityPrincipalId string
param tags object

// Built-in role definition id for "Search Index Data Contributor".
var searchIndexDataContributorRoleId = '8ebe5a00-799e-43f5-93ac-243d3dce84a7'

resource searchService 'Microsoft.Search/searchServices@2023-11-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'basic'
  }
  properties: {
    disableLocalAuth: true
    hostingMode: 'default'
    publicNetworkAccess: 'enabled'
  }
}

resource searchIndexDataContributorRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchService.id, managedIdentityPrincipalId, searchIndexDataContributorRoleId)
  scope: searchService
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      searchIndexDataContributorRoleId
    )
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

output endpoint string = 'https://${searchService.name}.search.windows.net'
output name string = searchService.name
