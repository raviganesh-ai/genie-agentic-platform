// Azure Key Vault: the only place secrets/connection material Genie's
// backend needs at runtime may live, per the Security Requirements in
// .github/copilot-instructions.md. RBAC authorization (not access
// policies) is used so the managed identity's Key Vault Secrets User role
// assignment is the single source of truth for who can read secrets.
param location string
param name string
param managedIdentityPrincipalId string
param tags object

// Built-in role definition id for "Key Vault Secrets User" - read-only
// access to secret contents, no ability to manage the vault itself.
var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    enablePurgeProtection: true
  }
}

resource secretsUserRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, managedIdentityPrincipalId, keyVaultSecretsUserRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      keyVaultSecretsUserRoleId
    )
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

output vaultUri string = keyVault.properties.vaultUri
output vaultName string = keyVault.name
