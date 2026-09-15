// Microsoft Entra ID (Azure AD) application used to secure INCOMING HTTP
// requests to the self-hosted Azure MCP Server deployed by
// finops-mcp-server.bicep, mirroring Microsoft's own verified reference
// pattern for exactly this scenario - Azure-Samples/azmcp-foundry-aca-mi's
// infra/modules/entra-app.bicep - adapted for Genie's own architecture:
// Genie's backend (not Azure AI Foundry natively) is the MCP client (see
// backend/app/agents/foundry/agent_provider.py's module docstring), so
// this module grants the custom app role directly to the caller of
// finops-mcp-server-role-assignment.bicep (Genie's backend managed
// identity) rather than to a Foundry project's own managed identity.
//
// Trimmed relative to the upstream sample: only the application-permission
// `appRoles` + Service Principal are created here (no delegated
// `oauth2PermissionScopes`/`preAuthorizedApplications` for interactive
// clients like VS Code - Genie's backend authenticates purely as a
// service, via its own managed identity's client-credentials token, never
// on behalf of an interactively signed-in user).
//
// Requires the deploying principal to hold Microsoft Graph application
// permissions (e.g. Application.ReadWrite.All, or the equivalent
// delegated admin consent) - a different privilege class than the ARM
// roles Genie's own CI/CD deploy identity holds today (see
// /memories/repo genie-project.md's CI/CD notes); deploy this module
// out-of-band with an operator identity that has that Graph permission,
// the same way Microsoft's own azd template expects.
//
// Deploy before finops-mcp-server.bicep and
// finops-mcp-server-role-assignment.bicep:
//   az deployment group create \
//     --resource-group <genie-resource-group> \
//     --template-file infra/modules/finops-mcp-server-entra-app.bicep \
//     --parameters entraAppDisplayName='Genie FinOps MCP Server' \
//                  entraAppUniqueName='genie-finops-mcp-server'
extension microsoftGraphV1

@description('Display name for the Entra Application.')
param entraAppDisplayName string

@description('Globally unique name for the Entra Application (e.g. genie-finops-mcp-server).')
param entraAppUniqueName string

var entraAppRoleValue = 'Mcp.Tools.ReadWrite.All'
var entraAppRoleId = guid(subscription().id, entraAppUniqueName, entraAppRoleValue)
var entraAppRoleDisplayName = 'Azure MCP Tools ReadWrite All'
var entraAppRoleDescription = 'Application permission for Azure MCP tool calls'

resource entraApp 'Microsoft.Graph/applications@v1.0' = {
  uniqueName: entraAppUniqueName
  displayName: entraAppDisplayName
  appRoles: [
    {
      id: entraAppRoleId
      displayName: entraAppRoleDisplayName
      description: entraAppRoleDescription
      value: entraAppRoleValue
      isEnabled: true
      allowedMemberTypes: [
        'Application'
      ]
    }
  ]
}

// A second resource referencing the same uniqueName, needed (exactly as
// in the upstream verified sample) because `identifierUris` requires the
// app's own `appId`, which is only known after `entraApp` above is created.
resource entraAppUpdate 'Microsoft.Graph/applications@v1.0' = {
  uniqueName: entraAppUniqueName
  displayName: entraAppDisplayName
  appRoles: entraApp.appRoles
  identifierUris: [
    'api://${entraApp.appId}'
  ]
}

resource entraServicePrincipal 'Microsoft.Graph/servicePrincipals@v1.0' = {
  appId: entraApp.appId
}

output entraAppClientId string = entraApp.appId
output entraAppObjectId string = entraApp.id
output entraAppIdentifierUri string = 'api://${entraApp.appId}'
output entraAppRoleId string = entraApp.appRoles[0].id
output entraAppServicePrincipalObjectId string = entraServicePrincipal.id
