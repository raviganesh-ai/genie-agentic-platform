const ICON_ROOT = "/azure-icons";

/** Fixed allowlist: model output selects a service name, never an arbitrary image URL. */
export const AZURE_SERVICE_ICONS: Readonly<Record<string, string>> = {
  "azure ai foundry": `${ICON_ROOT}/foundry-agent.svg`,
  "azure openai": `${ICON_ROOT}/azure-openai.svg`,
  "azure ai search": `${ICON_ROOT}/ai-search.svg`,
  "azure cognitive search": `${ICON_ROOT}/ai-search.svg`,
  "azure cosmos db": `${ICON_ROOT}/cosmos-db.svg`,
  "azure sql database": `${ICON_ROOT}/sql-database.svg`,
  "azure storage": `${ICON_ROOT}/storage-account.svg`,
  "azure blob storage": `${ICON_ROOT}/blob-storage.svg`,
  "azure key vault": `${ICON_ROOT}/key-vault.svg`,
  "azure container apps": `${ICON_ROOT}/container-apps.svg`,
  "azure static web apps": `${ICON_ROOT}/static-web-apps.svg`,
  "azure app service": `${ICON_ROOT}/app-service.svg`,
  "azure functions": `${ICON_ROOT}/functions.svg`,
  "azure api management": `${ICON_ROOT}/api-management.svg`,
  "azure service bus": `${ICON_ROOT}/service-bus.svg`,
  "azure event grid": `${ICON_ROOT}/event-grid.svg`,
  "azure monitor": `${ICON_ROOT}/monitor.svg`,
  "application insights": `${ICON_ROOT}/application-insights.svg`,
};

export function getAzureServiceIcon(service: string): string | undefined {
  return AZURE_SERVICE_ICONS[service.trim().toLowerCase()];
}