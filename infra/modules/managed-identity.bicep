// User-assigned managed identity: the single identity Genie's backend
// authenticates as against every other Azure resource (Key Vault, Storage,
// AI Search, Cosmos DB, Azure AI Foundry), per the Security Requirements
// (managed identity, least privilege) in .github/copilot-instructions.md.
param location string
param name string
param tags object

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: name
  location: location
  tags: tags
}

output principalId string = identity.properties.principalId
output clientId string = identity.properties.clientId
output resourceId string = identity.id
