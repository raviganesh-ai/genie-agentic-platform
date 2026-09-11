import fs from "node:fs";
import path from "node:path";
import process from "node:process";

const iconRoot = process.argv[2];

if (!iconRoot || !fs.existsSync(iconRoot)) {
  console.error(
    "Usage: node docs/architecture/generate-azure-diagrams.mjs <Azure_Public_Service_Icons/Icons>",
  );
  process.exit(1);
}

const outputDirectory = path.dirname(new URL(import.meta.url).pathname.replace(/^\/(.:)/, "$1"));

const iconFiles = new Map();

function indexIcons(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      indexIcons(entryPath);
    } else if (entry.name.endsWith(".svg") && !iconFiles.has(entry.name)) {
      iconFiles.set(entry.name, entryPath);
    }
  }
}

indexIcons(iconRoot);

const iconNames = {
  users: "10230-icon-service-Users.svg",
  staticWebApps: "01007-icon-service-Static-Apps.svg",
  appRegistration: "10232-icon-service-App-Registrations.svg",
  containerApps: "02989-icon-service-Container-Apps-Environments.svg",
  managedIdentity: "10227-icon-service-Entra-Managed-Identities.svg",
  foundryAgents: "038470523-icon-service-Foundry-Agent-Service.svg",
  aiSearch: "10044-icon-service-Cognitive-Search.svg",
  cosmosDb: "10121-icon-service-Azure-Cosmos-DB.svg",
  storage: "10086-icon-service-Storage-Accounts.svg",
  keyVault: "10245-icon-service-Key-Vaults.svg",
  containerRegistry: "10105-icon-service-Container-Registries.svg",
  applicationInsights: "00012-icon-service-Application-Insights.svg",
  monitor: "00001-icon-service-Monitor.svg",
  resourceGroup: "10007-icon-service-Resource-Groups.svg",
};

const icons = Object.fromEntries(
  Object.entries(iconNames).map(([key, fileName]) => {
    const iconPath = iconFiles.get(fileName);
    if (!iconPath) {
      throw new Error(`Official Azure icon not found: ${fileName}`);
    }
    const data = fs.readFileSync(iconPath).toString("base64");
    return [key, `data:image/svg+xml;base64,${data}`];
  }),
);

const colors = {
  azure: "#0078D4",
  azureDark: "#004578",
  azureLight: "#EFF6FC",
  border: "#B4C7D9",
  green: "#107C10",
  greenLight: "#F1FAF1",
  ink: "#172B3A",
  muted: "#526575",
  orange: "#D83B01",
  orangeLight: "#FFF4ED",
  purple: "#5C2D91",
  purpleLight: "#F7F3FA",
  surface: "#FFFFFF",
  teal: "#008575",
  tealLight: "#EAF8F6",
};

function escapeXml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function textBlock(x, y, lines, options = {}) {
  const {
    anchor = "start",
    color = colors.ink,
    fontSize = 16,
    fontWeight = 400,
    lineHeight = 21,
  } = options;
  return `<text x="${x}" y="${y}" text-anchor="${anchor}" fill="${color}" font-family="Segoe UI, sans-serif" font-size="${fontSize}" font-weight="${fontWeight}">${lines
    .map(
      (line, index) =>
        `<tspan x="${x}" dy="${index === 0 ? 0 : lineHeight}">${escapeXml(line)}</tspan>`,
    )
    .join("")}</text>`;
}

function serviceCard({
  x,
  y,
  width,
  height,
  icon,
  title,
  subtitle = [],
  accent = colors.azure,
  fill = colors.surface,
}) {
  const titleLines = Array.isArray(title) ? title : [title];
  const titleY = y + 34;
  const subtitleY = titleY + titleLines.length * 19 + 10;
  return `<g>
    <rect x="${x}" y="${y}" width="${width}" height="${height}" rx="8" fill="${fill}" stroke="${colors.border}" filter="url(#shadow)"/>
    <rect x="${x}" y="${y}" width="6" height="${height}" rx="3" fill="${accent}"/>
    <image href="${icons[icon]}" x="${x + 20}" y="${y + 23}" width="54" height="54" preserveAspectRatio="xMidYMid meet"/>
    ${textBlock(x + 90, titleY, titleLines, { fontSize: 16, fontWeight: 600, lineHeight: 19 })}
    ${textBlock(x + 90, subtitleY, subtitle, { color: colors.muted, fontSize: 13, lineHeight: 17 })}
  </g>`;
}

function serviceTile({
  x,
  y,
  width,
  height,
  icon,
  title,
  subtitle = [],
  accent = colors.teal,
}) {
  const titleLines = Array.isArray(title) ? title : [title];
  const titleY = y + 87;
  const subtitleY = titleY + titleLines.length * 18 + 8;
  return `<g>
    <rect x="${x}" y="${y}" width="${width}" height="${height}" rx="8" fill="${colors.surface}" stroke="${colors.border}" filter="url(#shadow)"/>
    <rect x="${x}" y="${y}" width="${width}" height="5" rx="2.5" fill="${accent}"/>
    <image href="${icons[icon]}" x="${x + width / 2 - 27}" y="${y + 18}" width="54" height="54" preserveAspectRatio="xMidYMid meet"/>
    ${textBlock(x + width / 2, titleY, titleLines, { anchor: "middle", fontSize: 15, fontWeight: 600, lineHeight: 18 })}
    ${textBlock(x + width / 2, subtitleY, subtitle, { anchor: "middle", color: colors.muted, fontSize: 12, lineHeight: 15 })}
  </g>`;
}

function componentCard({ x, y, width, height, title, subtitle = [], accent = colors.azure }) {
  return `<g>
    <rect x="${x}" y="${y}" width="${width}" height="${height}" rx="6" fill="${colors.surface}" stroke="${accent}" stroke-width="1.5"/>
    <rect x="${x}" y="${y}" width="5" height="${height}" rx="2.5" fill="${accent}"/>
    ${textBlock(x + 18, y + 27, [title], { fontSize: 15, fontWeight: 600 })}
    ${textBlock(x + 18, y + 49, subtitle, { color: colors.muted, fontSize: 12.5, lineHeight: 16 })}
  </g>`;
}

function zone({ x, y, width, height, title, subtitle, icon, fill = colors.azureLight, stroke = colors.azure }) {
  const iconMarkup = icon
    ? `<image href="${icons[icon]}" x="${x + 18}" y="${y + 16}" width="30" height="30" preserveAspectRatio="xMidYMid meet"/>`
    : "";
  const textX = x + (icon ? 58 : 20);
  return `<g>
    <rect x="${x}" y="${y}" width="${width}" height="${height}" rx="8" fill="${fill}" stroke="${stroke}" stroke-width="1.5"/>
    ${iconMarkup}
    ${textBlock(textX, y + 29, [title], { color: colors.azureDark, fontSize: 17, fontWeight: 600 })}
    ${subtitle ? textBlock(textX, y + 49, [subtitle], { color: colors.muted, fontSize: 12.5 }) : ""}
  </g>`;
}

function boundary({ x, y, width, height, title, subtitle }) {
  const subtitleWidth = subtitle ? subtitle.length * 6.7 + 20 : 0;
  return `<g>
    <rect x="${x}" y="${y}" width="${width}" height="${height}" rx="8" fill="#FAFCFE" stroke="${colors.azure}" stroke-width="2" stroke-dasharray="9 6"/>
    <rect x="${x + 18}" y="${y - 19}" width="430" height="38" rx="6" fill="${colors.surface}" stroke="${colors.azure}"/>
    <image href="${icons.resourceGroup}" x="${x + 31}" y="${y - 11}" width="22" height="22" preserveAspectRatio="xMidYMid meet"/>
    ${textBlock(x + 62, y + 6, [title], { color: colors.azureDark, fontSize: 15, fontWeight: 600 })}
    ${subtitle ? `<rect x="${x + 460}" y="${y - 14}" width="${subtitleWidth}" height="28" rx="4" fill="${colors.surface}"/>${textBlock(x + 470, y + 6, [subtitle], { color: colors.muted, fontSize: 12.5 })}` : ""}
  </g>`;
}

function arrow(pathData, options = {}) {
  const {
    color = colors.azure,
    dashed = false,
    label,
    labelX,
    labelY,
    marker = "arrow",
  } = options;
  return `<g>
    <path d="${pathData}" fill="none" stroke="${color}" stroke-width="2.2" ${dashed ? 'stroke-dasharray="7 5"' : ""} marker-end="url(#${marker})"/>
    ${label ? `<rect x="${labelX - label.length * 3.35 - 7}" y="${labelY - 14}" width="${label.length * 6.7 + 14}" height="22" rx="4" fill="#FFFFFF" opacity="0.96"/>${textBlock(labelX, labelY + 2, [label], { anchor: "middle", color, fontSize: 12.5, fontWeight: 600 })}` : ""}
  </g>`;
}

function legend(x, y) {
  return `<g>
    <path d="M ${x} ${y} H ${x + 48}" stroke="${colors.azure}" stroke-width="2.2" marker-end="url(#arrow)"/>
    ${textBlock(x + 60, y + 5, ["Application request / data flow"], { color: colors.muted, fontSize: 12.5 })}
    <path d="M ${x + 260} ${y} H ${x + 308}" stroke="${colors.purple}" stroke-width="2.2" stroke-dasharray="7 5" marker-end="url(#arrowPurple)"/>
    ${textBlock(x + 320, y + 5, ["Identity / deployment control"], { color: colors.muted, fontSize: 12.5 })}
  </g>`;
}

function svgDocument({ title, description, width, height, content }) {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img" aria-labelledby="title description">
  <title id="title">${escapeXml(title)}</title>
  <desc id="description">${escapeXml(description)}</desc>
  <defs>
    <filter id="shadow" x="-20%" y="-20%" width="140%" height="140%">
      <feDropShadow dx="0" dy="2" stdDeviation="3" flood-color="#36566F" flood-opacity="0.16"/>
    </filter>
    <marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
      <path d="M0,0 L0,6 L9,3 z" fill="${colors.azure}"/>
    </marker>
    <marker id="arrowPurple" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
      <path d="M0,0 L0,6 L9,3 z" fill="${colors.purple}"/>
    </marker>
    <marker id="arrowGreen" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
      <path d="M0,0 L0,6 L9,3 z" fill="${colors.green}"/>
    </marker>
  </defs>
  <rect width="${width}" height="${height}" fill="#F7FAFC"/>
  ${content}
</svg>`;
}

function platformDiagram() {
  const connectors = [
    arrow("M 190 347 H 290", { label: "HTTPS", labelX: 238, labelY: 333 }),
    arrow("M 470 312 H 490", { color: colors.purple, dashed: true, marker: "arrowPurple" }),
    arrow("M 470 347 H 710 V 270 H 755", { label: "Anonymous HTTPS", labelX: 600, labelY: 333 }),
    arrow("M 955 315 V 340", { label: "Private route", labelX: 1010, labelY: 333 }),
    arrow("M 1005 391 H 1030"),
    arrow("M 1093 442 V 465", { label: "Invoke", labelX: 1125, labelY: 456 }),
    arrow("M 900 577 V 610 H 250 V 720 H 290", { color: colors.teal, label: "Agent calls", labelX: 650, labelY: 601 }),
    arrow("M 970 577 V 625", { color: colors.teal, label: "Memory + artifacts", labelX: 1030, labelY: 608 }),
    arrow("M 1260 510 H 1188 V 530 H 1155", { color: colors.purple, dashed: true, label: "Workload identity", labelX: 1205, labelY: 496, marker: "arrowPurple" }),
    arrow("M 1260 600 H 1200 V 450 H 1015 V 391 H 1005", { color: colors.purple, dashed: true, label: "Pull images", labelX: 1105, labelY: 464, marker: "arrowPurple" }),
    arrow("M 1155 530 H 1198 V 740 H 1260", { color: colors.green, label: "Telemetry", labelX: 1205, labelY: 726, marker: "arrowGreen" }),
    arrow("M 1372 790 V 830", { color: colors.green, marker: "arrowGreen" }),
  ].join("");

  const content = `
    ${textBlock(48, 52, ["Genie Azure platform"], { color: colors.azureDark, fontSize: 30, fontWeight: 700 })}
    ${textBlock(48, 80, ["Internal evaluation environment | Azure-native identity, compute, AI, data, and operations"], { color: colors.muted, fontSize: 15 })}
    <rect x="1258" y="34" width="294" height="38" rx="5" fill="#FFF4CE" stroke="#D6B656"/>
    ${textBlock(1405, 58, ["MICROSOFT CONFIDENTIAL"], { anchor: "middle", color: "#6B5700", fontSize: 14, fontWeight: 700 })}
    ${boundary({ x: 230, y: 120, width: 1322, height: 796, title: "Azure subscription / Genie resource group", subtitle: "Foundational resources provisioned with Bicep" })}
    ${zone({ x: 270, y: 165, width: 420, height: 250, title: "Experience and access", subtitle: "Public web experience; no sign-in required", fill: colors.azureLight })}
    ${zone({ x: 730, y: 165, width: 450, height: 420, title: "Gateway and private runtime", subtitle: "Public APIM edge; private Container Apps endpoint", icon: "containerApps", fill: colors.greenLight, stroke: colors.green })}
    ${zone({ x: 1215, y: 165, width: 295, height: 650, title: "Platform operations", subtitle: "Identity, images, and telemetry", fill: colors.purpleLight, stroke: colors.purple })}
    ${zone({ x: 270, y: 585, width: 910, height: 275, title: "Azure AI and data services", subtitle: "Private workload dependencies reached with managed identity", fill: colors.tealLight, stroke: colors.teal })}
    ${connectors}
    ${serviceCard({ x: 290, y: 265, width: 180, height: 120, icon: "staticWebApps", title: ["Azure Static", "Web Apps"], subtitle: ["React Mission", "Control UI"] })}
    ${serviceCard({ x: 490, y: 265, width: 180, height: 120, icon: "users", title: ["Anonymous", "internal access"], subtitle: ["No sign-in", "Shared principal"], accent: colors.purple })}
    ${serviceCard({ x: 50, y: 292, width: 140, height: 110, icon: "users", title: "Collaborator", subtitle: ["Internal user"], accent: colors.purple })}
    ${componentCard({ x: 755, y: 225, width: 400, height: 90, title: "Azure API Management Standard v2", subtitle: ["Only public API endpoint", "Exact CORS + rate limit + correlation"], accent: colors.azure })}
    ${componentCard({ x: 755, y: 340, width: 250, height: 102, title: "FastAPI API", subtitle: ["Private endpoint :8000", "Private DNS + API routes"], accent: colors.green })}
    ${componentCard({ x: 1030, y: 340, width: 125, height: 102, title: "Identity", subtitle: ["genie-internal-user", "Genie.Admin"], accent: colors.orange })}
    ${componentCard({ x: 755, y: 465, width: 400, height: 112, title: "Workflow orchestration", subtitle: ["Governance + three-tier memory", "AzureAgentGateway + Deploy & Launch"], accent: colors.azure })}
    ${serviceCard({ x: 1260, y: 400, width: 225, height: 114, icon: "managedIdentity", title: ["User-assigned", "managed identity"], subtitle: ["Least-privilege RBAC"], accent: colors.purple })}
    ${serviceCard({ x: 1260, y: 545, width: 225, height: 114, icon: "containerRegistry", title: ["Azure Container", "Registry"], subtitle: ["Commit-pinned images"], accent: colors.purple })}
    ${serviceCard({ x: 1260, y: 690, width: 225, height: 100, icon: "applicationInsights", title: ["Application", "Insights"], subtitle: ["OpenTelemetry"], accent: colors.green })}
    ${serviceCard({ x: 1278, y: 830, width: 190, height: 64, icon: "monitor", title: "Azure Monitor", subtitle: [], accent: colors.green })}
    ${serviceTile({ x: 290, y: 640, width: 160, height: 165, icon: "foundryAgents", title: ["Foundry Agent", "Service"], subtitle: ["Prompt Agents", "Model deployments"] })}
    ${serviceTile({ x: 465, y: 640, width: 160, height: 165, icon: "aiSearch", title: ["Azure AI Search"], subtitle: ["Enterprise", "knowledge"] })}
    ${serviceTile({ x: 640, y: 640, width: 160, height: 165, icon: "cosmosDb", title: ["Azure Cosmos DB"], subtitle: ["Sessions, memory", "and lineage"] })}
    ${serviceTile({ x: 815, y: 640, width: 160, height: 165, icon: "storage", title: ["Azure Storage"], subtitle: ["Uploads and", "artifacts"] })}
    ${serviceTile({ x: 990, y: 640, width: 160, height: 165, icon: "keyVault", title: ["Azure Key Vault"], subtitle: ["Secret references"] })}
    ${legend(290, 888)}
  `;

  return svgDocument({
    title: "Genie Azure platform architecture",
    description: "Azure architecture diagram showing anonymous internal users, Static Web Apps, a public API Management edge, a private Container Apps FastAPI runtime, Foundry Agent Service, data services, managed identity, Container Registry, Application Insights, and Azure Monitor.",
    width: 1600,
    height: 960,
    content,
  });
}

function prototypeDiagram() {
  const connectors = [
    arrow("M 200 175 H 740 V 253", { label: "Open prototype", labelX: 470, labelY: 161 }),
    arrow("M 825 315 V 365", { label: "API request", labelX: 882, labelY: 345 }),
    arrow("M 825 485 V 535", { label: "Private route", labelX: 885, labelY: 515 }),
    arrow("M 1150 605 H 1185 V 307 H 1220", { color: colors.teal }),
    arrow("M 545 230 V 215 H 740 V 270 H 765", { color: colors.purple, dashed: true, marker: "arrowPurple" }),
    arrow("M 700 335 H 720 V 425 H 765", { color: colors.purple, dashed: true, marker: "arrowPurple" }),
    arrow("M 700 500 H 720 V 770 H 1180 V 307 H 1220", { color: colors.purple, dashed: true, label: "Workload identity + least-privilege RBAC", labelX: 950, labelY: 756, marker: "arrowPurple" }),
    arrow("M 1180 455 H 1220", { color: colors.purple, dashed: true, marker: "arrowPurple" }),
    arrow("M 1220 455 H 1175 V 270 H 1150", { label: "Images", labelX: 1185, labelY: 256 }),
    arrow("M 1125 635 H 1215", { color: colors.green, marker: "arrowGreen" }),
    arrow("M 1352 675 V 705", { color: colors.green, marker: "arrowGreen" }),
    arrow("M 275 450 H 320", { color: colors.purple, dashed: true, marker: "arrowPurple" }),
  ].join("");

  const content = `
    ${textBlock(48, 52, ["Generated prototype isolation"], { color: colors.azureDark, fontSize: 30, fontWeight: 700 })}
    ${textBlock(48, 80, ["One independently secured Azure runtime boundary per prototype"], { color: colors.muted, fontSize: 15 })}
    <rect x="1258" y="34" width="294" height="38" rx="5" fill="#FFF4CE" stroke="#D6B656"/>
    ${textBlock(1405, 58, ["MICROSOFT CONFIDENTIAL"], { anchor: "middle", color: "#6B5700", fontSize: 14, fontWeight: 700 })}
    ${boundary({ x: 320, y: 120, width: 1232, height: 780, title: "Azure subscription / dedicated prototype resource group", subtitle: "Gateway, network, runtime, identity, and data delete as one owned boundary" })}
    ${zone({ x: 365, y: 175, width: 350, height: 630, title: "Identity and lifecycle", subtitle: "Anonymous user access; prototype workload identity", fill: colors.purpleLight, stroke: colors.purple })}
    ${zone({ x: 740, y: 175, width: 435, height: 630, title: "Dedicated gateway and runtime", subtitle: "Public APIM edge; private FastAPI network", icon: "containerApps", fill: colors.greenLight, stroke: colors.green })}
    ${zone({ x: 1200, y: 175, width: 310, height: 630, title: "Workload dependencies", subtitle: "Prototype-owned agents and evidence", fill: colors.tealLight, stroke: colors.teal })}
    ${connectors}
    ${serviceCard({ x: 45, y: 120, width: 155, height: 110, icon: "users", title: "Prototype user", subtitle: ["No sign-in required"], accent: colors.purple })}
    ${componentCard({ x: 45, y: 300, width: 230, height: 270, title: "Genie Deploy & Launch", subtitle: ["Deterministic provisioning", "90% executable coverage gate", "100% executable tests pass", "Approval and governance trace", "Owner-authorized teardown"], accent: colors.azure })}
    ${serviceCard({ x: 390, y: 230, width: 310, height: 150, icon: "users", title: ["Anonymous prototype", "access"], subtitle: ["No Entra application", "No MSAL or bearer token", "No shared callback slot"], accent: colors.purple })}
    ${serviceCard({ x: 390, y: 425, width: 310, height: 150, icon: "managedIdentity", title: ["Dedicated mission", "managed identity"], subtitle: ["AcrPull at exact registry scope", "Least-privilege workload RBAC", "RBAC removed before identity"], accent: colors.purple })}
    ${componentCard({ x: 390, y: 625, width: 310, height: 115, title: "Prototype access policy", subtitle: ["Exact browser CORS origin", "Owner authorization for abandonment"], accent: colors.purple })}
    ${serviceCard({ x: 765, y: 225, width: 385, height: 90, icon: "containerApps", title: "Prototype frontend Container App", subtitle: ["Anonymous HTTPS ingress"], accent: colors.green })}
    ${componentCard({ x: 765, y: 365, width: 385, height: 120, title: "Dedicated Azure API Management", subtitle: ["Only public API endpoint", "Exact CORS + rate limit + correlation"], accent: colors.azure })}
    ${componentCard({ x: 765, y: 535, width: 385, height: 140, title: "Private VNet + Container Apps environment", subtitle: ["Generated FastAPI has internal ingress only", "Private DNS; no public backend route"], accent: colors.orange })}
    ${serviceCard({ x: 1220, y: 235, width: 265, height: 130, icon: "foundryAgents", title: ["Dedicated Foundry", "Agent Service agents"], subtitle: ["Specialists + orchestrator"], accent: colors.teal })}
    ${serviceCard({ x: 1220, y: 395, width: 265, height: 120, icon: "containerRegistry", title: ["Azure Container", "Registry"], subtitle: ["Immutable prototype images"], accent: colors.teal })}
    ${serviceCard({ x: 1220, y: 545, width: 265, height: 130, icon: "applicationInsights", title: ["Governance and", "operational evidence"], subtitle: ["Approvals, fidelity, tests", "Logs, metrics, traces"], accent: colors.green })}
    ${serviceCard({ x: 1220, y: 705, width: 265, height: 70, icon: "monitor", title: "Azure Monitor", subtitle: [], accent: colors.green })}
    ${legend(390, 855)}
  `;

  return svgDocument({
    title: "Generated prototype Azure isolation architecture",
    description: "Azure architecture diagram showing anonymous prototype access through a dedicated API Management service, managed identity, private VNet and Container Apps backend, Foundry Agent Service agents, Container Registry, governance evidence, and Azure Monitor for each generated prototype.",
    width: 1600,
    height: 940,
    content,
  });
}

const outputs = [
  ["genie-azure-platform.svg", platformDiagram()],
  ["generated-prototype-isolation.svg", prototypeDiagram()],
];

for (const [fileName, content] of outputs) {
  const outputPath = path.join(outputDirectory, fileName);
  fs.writeFileSync(outputPath, content, "utf8");
  console.log(`Generated ${outputPath}`);
}