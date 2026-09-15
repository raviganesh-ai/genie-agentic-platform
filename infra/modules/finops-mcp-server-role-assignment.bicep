// Grants a principal (Genie's own backend managed identity - see
// infra/modules/managed-identity.bicep - NOT a Foundry project's identity,
// since Genie's backend is the actual MCP client here; see
// backend/app/agents/foundry/agent_provider.py's module docstring) the
// custom Microsoft Entra ID app role created by
// finops-mcp-server-entra-app.bicep, so its Microsoft Entra ID access
// tokens for that app's audience are accepted by the self-hosted Azure
// MCP Server (finops-mcp-server.bicep) as authorized incoming requests.
//
// Mirrors Microsoft's own verified reference pattern for this exact app
// role assignment shape - Azure-Samples/azmcp-foundry-aca-mi's
// infra/modules/foundry-role-assignment-entraapp.bicep - generalized to
// accept any principal ID rather than assuming a Foundry project's
// identity.
//
// Requires the same Microsoft Graph application permission as
// finops-mcp-server-entra-app.bicep to deploy (see that module's header).
extension microsoftGraphV1

@description('Object (principal) ID of the identity to grant the app role to - e.g. the Genie backend managed identity\'s principalId (infra/modules/managed-identity.bicep output).')
param granteePrincipalId string

@description('Entra App Service Principal object ID (finops-mcp-server-entra-app.bicep output entraAppServicePrincipalObjectId).')
param entraAppServicePrincipalObjectId string

@description('Entra App custom role ID to assign (finops-mcp-server-entra-app.bicep output entraAppRoleId).')
param entraAppRoleId string

resource appRoleAssignment 'Microsoft.Graph/appRoleAssignedTo@v1.0' = {
  principalId: granteePrincipalId
  resourceId: entraAppServicePrincipalObjectId
  appRoleId: entraAppRoleId
}

output roleAssignmentId string = appRoleAssignment.id
