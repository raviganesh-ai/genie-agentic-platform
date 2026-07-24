// Application Insights (workspace-based) for backend request/dependency
// telemetry and OpenTelemetry export, per the Backend Standards in
// .github/copilot-instructions.md.
param location string
param name string
param logAnalyticsWorkspaceId string
param tags object

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: name
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalyticsWorkspaceId
    IngestionMode: 'LogAnalytics'
  }
}

output connectionString string = appInsights.properties.ConnectionString
output instrumentationKey string = appInsights.properties.InstrumentationKey
