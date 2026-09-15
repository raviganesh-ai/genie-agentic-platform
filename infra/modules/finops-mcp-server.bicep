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
// `containerAppsEnvironmentId` output). It requires an Entra App
// Registration deployed first (finops-mcp-server-entra-app.bicep) and a
// role assignment granting the Genie backend's own managed identity that
// app's custom role (finops-mcp-server-role-assignment.bicep) - see
// those modules' own headers - then:
//   az deployment group create \
//     --resource-group <genie-resource-group> \
//     --template-file infra/modules/finops-mcp-server.bicep \
//     --parameters containerAppsEnvironmentId=<id> \
//                  entraAppClientId=<from finops-mcp-server-entra-app output> \
//                  entraAppTenantId=<tenant-id> ...
//
// Container image, entrypoint, arguments, listening port, and required
// environment variables below are copied/adapted (single `kusto`
// namespace, `--read-only`, no VS Code/PRM-client support since only
// Genie's own backend ever connects) from Microsoft's own verified
// reference deployment for exactly this scenario - Azure MCP Server on
// Container Apps for Foundry-agent consumption -
// https://github.com/Azure-Samples/azmcp-foundry-aca-mi
// (infra/modules/aca-infrastructure.bicep), per the "never invent
// undocumented APIs" rule: nothing below is guessed.
//
// SECURITY NOTE: the container is started with `--read-only` (Genie's
// FinOps hub tool only ever queries, never mutates) and Microsoft Entra ID
// authentication REMAINS ENABLED on every incoming HTTP request (the
// image's default posture) - never add
// `--dangerously-disable-http-incoming-auth` to containerArgs. Genie's
// own backend (not Foundry natively - see
// backend/app/agents/foundry/agent_provider.py's module docstring on why
// Genie's MCP integration differs from azmcp-foundry-aca-mi's "native
// Foundry Tools" connection pattern) authenticates its outgoing MCP
// requests with a Microsoft Entra ID access token for this server's own
// Entra App Registration audience, acquired via its own managed identity
// (AgentMcpToolDefinition.client_id_setting /
// FoundryAgentProvider._build_mcp_header_provider) - never a shared
// secret or disabled auth.
@description('Azure region for the Container App (must match the Container Apps environment\'s region).')
param location string

@description('Container App name, e.g. genie-finops-mcp-server.')
param name string

@description('Resource ID of the existing Container Apps environment (see infra/main.bicep output containerAppsEnvironmentId).')
param containerAppsEnvironmentId string

@description('Full container image reference, e.g. mcr.microsoft.com/azure-sdk/azure-mcp:<verified-tag> - never `:latest` in production, per immutable-deployment practice.')
param containerImage string

@description('Azure MCP Server namespaces to expose (kusto-only by default, since Genie only ever calls the Kusto query tool). Passed as one repeated --namespace flag per entry.')
param namespaces string[] = [
  'kusto'
]

@description('TCP port the container listens on for MCP HTTP traffic. 8080 is the port the upstream image\'s ASPNETCORE_URLS/ingress targetPort are verified to use - do not change unless the image itself changes.')
param containerPort int = 8080

@description('Microsoft Entra ID application (client) ID of this server\'s own Entra App Registration (finops-mcp-server-entra-app.bicep output entraAppClientId) - used for incoming-request token validation (AzureAd__ClientId).')
param entraAppClientId string

@description('Microsoft Entra ID tenant ID the Entra App Registration above was created in (AzureAd__TenantId).')
param entraAppTenantId string

@description('Application Insights connection string for the MCP server\'s own telemetry. Leave empty to disable.')
param appInsightsConnectionString string = ''

@description('Whether azure-mcp reports its own anonymous usage telemetry to Microsoft (AZURE_MCP_COLLECT_TELEMETRY) - defaults to false since this is a customer/operator deployment, not a Microsoft-operated one.')
param collectTelemetry bool = false

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

// Verified server-start arguments (Azure-Samples/azmcp-foundry-aca-mi's
// infra/modules/aca-infrastructure.bicep `baseArgs`/`serverArgs`), adapted
// to this deployment's read-only, Kusto-only scope. The container image's
// own ENTRYPOINT already runs `server start` - `command` is intentionally
// left empty (the image does not support overriding it; see
// https://github.com/microsoft/mcp/blob/main/servers/Azure.Mcp.Server/docs/azmcp-commands.md's
// "Using azmcp locally vs in container images" section).
var baseArgs = [
  '--transport'
  'http'
  '--outgoing-auth-strategy'
  'UseHostingEnvironmentIdentity'
  '--mode'
  'all'
  // SECURITY NOTE: read-only - this deployment only ever needs to query
  // the operator's FinOps hub, never mutate Azure resources through it.
  '--read-only'
  // SECURITY NOTE: never add '--dangerously-disable-http-incoming-auth' -
  // see this module's header.
]
var namespaceArgs = [for ns in namespaces: ['--namespace', ns]]
var serverArgs = flatten(concat([baseArgs], namespaceArgs))

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
      // public internet. Microsoft Entra ID auth (AzureAd__* below) is
      // still enforced as defense-in-depth on top of this network
      // boundary, exactly as the upstream reference deployment does for
      // its own (public/external) ingress.
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
          command: []
          args: serverArgs
          resources: {
            cpu: json(cpuCores)
            memory: memory
          }
          env: concat(
            [
              {
                name: 'ASPNETCORE_ENVIRONMENT'
                value: 'Production'
              }
              {
                name: 'ASPNETCORE_URLS'
                value: 'http://+:${containerPort}'
              }
              {
                // Authenticates OUTGOING requests to Azure/Kusto via this
                // Container App's own user-assigned managed identity -
                // never a client secret or connection string (per
                // Security Requirements).
                name: 'AZURE_TOKEN_CREDENTIALS'
                value: 'managedidentitycredential'
              }
              {
                name: 'AZURE_CLIENT_ID'
                value: identity.outputs.clientId
              }
              {
                name: 'AZURE_MCP_INCLUDE_PRODUCTION_CREDENTIALS'
                value: 'true'
              }
              {
                name: 'AZURE_MCP_COLLECT_TELEMETRY'
                value: string(collectTelemetry)
              }
              {
                // Secures INCOMING requests: only callers presenting a
                // valid Microsoft Entra ID token for this app's own
                // audience (api://<entraAppClientId>) are accepted - see
                // this module's header and
                // finops-mcp-server-entra-app.bicep.
                name: 'AzureAd__Instance'
                value: environment().authentication.loginEndpoint
              }
              {
                name: 'AzureAd__TenantId'
                value: entraAppTenantId
              }
              {
                name: 'AzureAd__ClientId'
                value: entraAppClientId
              }
              {
                name: 'AZURE_LOG_LEVEL'
                value: 'Verbose'
              }
              // SECURITY NOTE: azure-mcp listens on plain HTTP inside the
              // pod (ASPNETCORE_URLS above); Container Apps' own Envoy
              // proxy terminates TLS at the ingress boundary before
              // routing internally over HTTP within the secure pod
              // network namespace, so external/environment traffic never
              // leaves TLS. See
              // https://learn.microsoft.com/en-us/azure/container-apps/ingress-overview
              {
                name: 'AZURE_MCP_DANGEROUSLY_DISABLE_HTTPS_REDIRECTION'
                value: 'true'
              }
            ],
            !empty(appInsightsConnectionString)
              ? [
                  {
                    name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
                    value: appInsightsConnectionString
                  }
                ]
              : []
          )
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

