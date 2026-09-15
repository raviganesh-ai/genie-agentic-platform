// Self-hosted Azure MCP Server (mcr.microsoft.com/azure-sdk/azure-mcp,
// https://github.com/microsoft/mcp) Container App, exposing the Kusto
// query tool that the finops-hub-agent Foundry agent calls (via
// agent_framework.MCPStreamableHTTPTool - see
// backend/app/agents/foundry/agent_provider.py and
// /memories/repo/mcp-tool-integration.md) to read an operator's own
// FinOps toolkit hub (a separate, customer/operator-deployed Azure Data
// Explorer cluster - Genie never provisions the hub itself, only ever
// connects to one that already exists).
//
// NOT wired into infra/main.bicep - this is an OPTIONAL module, deployed
// only by operators who have chosen to enable the FinOps-hub-agent path
// (GENIE_FINOPS_HUB_MCP_SERVER_URL) rather than the default Azure Cost
// Management fallback. Deploy it into the SAME resource group/Container
// Apps environment as the Genie backend (see infra/main.bicep's
// `containerAppsEnvironmentId` output) via:
//   az deployment group create \
//     --resource-group <genie-resource-group> \
//     --template-file infra/modules/finops-mcp-server.bicep \
//     --parameters containerAppsEnvironmentId=<id> ...
//
// ASSUMPTION CALLED OUT EXPLICITLY (per .github/copilot-instructions.md's
// "never invent undocumented APIs" / "isolate assumptions behind
// interfaces" rules): the exact container `command`/`args` needed to run
// azure-mcp in remote HTTP/streamable-HTTP mode (vs. its default stdio
// mode) and the port it listens on were NOT independently verified against
// a real deployment in this session - the public docs (microsoft/mcp's
// Azure.Mcp.Server README, "Remote Setup" section) point to azd templates
// under servers/Azure.Mcp.Server/azd-templates for the verified,
// supported values instead of documenting them inline. Rather than
// guessing and silently shipping a wrong default, `containerCommand`,
// `containerArgs`, and `containerPort` are REQUIRED parameters here with
// no default - populate them from that azd template (or `azmcp server
// start --help`) before deploying.
@description('Azure region for the Container App (must match the Container Apps environment\'s region).')
param location string

@description('Container App name, e.g. genie-finops-mcp-server.')
param name string

@description('Resource ID of the existing Container Apps environment (see infra/main.bicep output containerAppsEnvironmentId).')
param containerAppsEnvironmentId string

@description('Full container image reference, e.g. mcr.microsoft.com/azure-sdk/azure-mcp:<verified-tag> - never `:latest` in production, per immutable-deployment practice.')
param containerImage string

@description('Container entrypoint override, if the image requires one to start in remote/HTTP mode - see module header ASSUMPTION note.')
param containerCommand string[]

@description('Container arguments to start azure-mcp in remote/HTTP (streamable) mode restricted to the Kusto tool namespace - see module header ASSUMPTION note.')
param containerArgs string[]

@description('TCP port the container listens on for MCP HTTP/SSE traffic - see module header ASSUMPTION note.')
param containerPort int

@description('Tags applied to every resource this module creates.')
param tags object

@description('Minimum replica count. Defaults to 0 (scales to zero when idle) since this is only called during the informational-only finops-cost-report step, never on the request hot path.')
param minReplicas int = 0

@description('Maximum replica count.')
param maxReplicas int = 1

@description('vCPU cores allocated to the container.')
param cpuCores string = '0.5'

@description('Memory allocated to the container.')
param memory string = '1Gi'

// Dedicated, least-privilege identity - deliberately separate from the
// Genie backend's own managed identity (infra/modules/managed-identity.bicep):
// this identity's only real permission need is a Kusto-native database
// security role grant on the operator's own FinOps hub cluster (an
// out-of-band `.add database <Hub> viewers ('aadapp=<principalId>')` Kusto
// management command against that cluster, not an ARM role assignment -
// see /memories/repo/mcp-tool-integration.md), so it must not inherit any
// of the backend identity's broader Azure resource access.
module identity 'managed-identity.bicep' = {
  name: '${name}-identity'
  params: {
    location: location
    name: '${name}-identity'
    tags: tags
  }
}

resource mcpServerContainerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      // Computed via resourceId() (deterministic from the template's own
      // inputs) rather than the identity module's own resourceId output,
      // which Bicep/ARM disallows here (BCP120: the `identity` property's
      // keys must be calculable at the start of deployment).
      '${resourceId('Microsoft.ManagedIdentity/userAssignedIdentities', '${name}-identity')}': {}
    }
  }
  properties: {
    environmentId: containerAppsEnvironmentId
    configuration: {
      // Internal-only ingress: only reachable from other apps in the same
      // Container Apps environment (i.e. the Genie backend, which is what
      // calls this server's MCP endpoint via
      // agent_framework.MCPStreamableHTTPTool) - never exposed to the
      // public internet.
      ingress: {
        external: false
        targetPort: containerPort
        transport: 'http'
        allowInsecure: false
      }
      activeRevisionsMode: 'Single'
    }
    template: {
      containers: [
        {
          name: 'azure-mcp-server'
          image: containerImage
          command: containerCommand
          args: containerArgs
          resources: {
            cpu: json(cpuCores)
            memory: memory
          }
          env: [
            {
              // Authenticates to Azure/Kusto via this Container App's own
              // user-assigned managed identity - never a client secret or
              // connection string (per Security Requirements).
              name: 'AZURE_CLIENT_ID'
              value: identity.outputs.clientId
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
      }
    }
  }
}

@description('The internal HTTPS endpoint to set as GENIE_FINOPS_HUB_MCP_SERVER_URL.')
output url string = 'https://${mcpServerContainerApp.properties.configuration.ingress.fqdn}'
output identityPrincipalId string = identity.outputs.principalId
output identityClientId string = identity.outputs.clientId
