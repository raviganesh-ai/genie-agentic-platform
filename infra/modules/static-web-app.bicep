// Azure Static Web App: hosts the Mission Control React frontend, per the
// Frontend Standards in .github/copilot-instructions.md.
param location string
param name string
param tags object

resource staticWebApp 'Microsoft.Web/staticSites@2023-12-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Standard'
    tier: 'Standard'
  }
  properties: {
    // Deployment source is wired up separately (CI/CD pipeline or manual
    // `az staticwebapp` deployment), never a hardcoded repository URL here.
    provider: 'None'
  }
}

output defaultHostname string = staticWebApp.properties.defaultHostname
output name string = staticWebApp.name
