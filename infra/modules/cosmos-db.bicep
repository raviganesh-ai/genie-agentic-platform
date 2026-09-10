// Cosmos DB (SQL API, serverless): durable store for Personal Agent
// Memory, Shared Collaboration Memory, and governance lineage, per the
// Memory Architecture and Governance Requirements in
// .github/copilot-instructions.md. Data-plane access is granted to the
// managed identity via a Cosmos DB SQL role assignment (Cosmos DB has its
// own RBAC system, separate from Microsoft.Authorization/roleAssignments).
param location string
param name string
param managedIdentityPrincipalId string
param virtualNetworkId string
param privateEndpointSubnetId string
param tags object

// Built-in Cosmos DB SQL role definition id for "Cosmos DB Built-in Data
// Contributor" (read/write data-plane access, no control-plane access).
var cosmosDataContributorRoleId = '00000000-0000-0000-0000-000000000002'

resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: name
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    locations: [
      {
        locationName: location
        failoverPriority: 0
      }
    ]
    capabilities: [
      {
        name: 'EnableServerless'
      }
    ]
    disableLocalAuth: true
    minimalTlsVersion: 'Tls12'
    publicNetworkAccess: 'Disabled'
    networkAclBypass: 'None'
  }
}

module privateEndpoint 'cosmos-private-endpoint.bicep' = {
  name: 'genie-cosmos-private-endpoint'
  params: {
    location: location
    cosmosAccountId: cosmosAccount.id
    cosmosAccountName: cosmosAccount.name
    virtualNetworkId: virtualNetworkId
    privateEndpointSubnetId: privateEndpointSubnetId
    tags: tags
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: cosmosAccount
  name: 'genie'
  properties: {
    resource: {
      id: 'genie'
    }
  }
}

resource memoryContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'memory'
  properties: {
    resource: {
      id: 'memory'
      partitionKey: {
        paths: [
          '/partitionKey'
        ]
        kind: 'Hash'
      }
    }
  }
}

resource dataContributorRoleAssignment 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  parent: cosmosAccount
  name: guid(cosmosAccount.id, managedIdentityPrincipalId, cosmosDataContributorRoleId)
  properties: {
    roleDefinitionId: '${cosmosAccount.id}/sqlRoleDefinitions/${cosmosDataContributorRoleId}'
    principalId: managedIdentityPrincipalId
    scope: cosmosAccount.id
  }
}

output endpoint string = cosmosAccount.properties.documentEndpoint
output accountName string = cosmosAccount.name
