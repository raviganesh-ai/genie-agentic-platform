import { useState } from "react";
import { Text } from "@fluentui/react-components";
import { splitIntoNamedSections } from "@/utils/textArtifacts";

const KEYWORD_ICONS: Array<[RegExp, string]> = [
  [/container app|kubernetes|\baks\b|app service|azure function|serverless/i, "⚙️"],
  [/cosmos|azure sql|\bsql\b|database|storage account|\bblob\b|data lake/i, "🗄️"],
  [/key vault|secret|entra|managed identity|\biam\b|rbac/i, "🔐"],
  [/cognitive search|\bai search\b|search index/i, "🔎"],
  [/monitor|insights|log analytics|telemetry|observability/i, "📈"],
  [/network|vnet|firewall|gateway|front door|cdn/i, "🌐"],
  [/foundry|openai|\bllm\b|model deployment|agent/i, "🤖"],
  [/queue|event grid|service bus|event hub|topic/i, "📨"],
  [/static web app|frontend|react|ui\b/i, "🖥️"],
  [/api\b|backend/i, "🔌"],
];

function iconForComponent(title: string): string {
  for (const [regex, icon] of KEYWORD_ICONS) {
    if (regex.test(title)) return icon;
  }
  return "🧩";
}

/**
 * Renders one architecture recommendation's free text as an actual
 * left-to-right component diagram - a card per named component (parsed via
 * `splitIntoNamedSections`) with an icon guessed from its name, connected
 * by arrows, click-to-expand rationale - instead of a single prose block.
 * Falls back to the raw text (still shown, just not diagrammed) when the
 * agent's response doesn't contain recognizable named sections.
 *
 * `animated`, when set, upgrades the plain static arrows/cards to the same
 * "alive" flow-diagram treatment as InteractiveFlowDiagram (glowing hover
 * nodes, a traveling data particle on every connector, staggered entrance)
 * - reserved for the sections that most deserve to visually pop (see
 * ArchitectureStudioPage's UI Design / Multi-Agent Workflow sections).
 */
export function ArchitectureComponentDiagram({
  content,
  animated,
}: {
  content: string;
  animated?: boolean;
}): JSX.Element {
  const [expanded, setExpanded] = useState<number | null>(0);
  const sections = splitIntoNamedSections(content);

  if (sections.length === 0) {
    return (
      <Text size={300} style={{ whiteSpace: "pre-wrap" }}>
        {content}
      </Text>
    );
  }

  return (
    <div>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 4, marginBottom: 12 }}>
        {sections.map((section, index) => (
          <div key={index} style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <button
              type="button"
              onClick={() => setExpanded((prev) => (prev === index ? null : index))}
              className={animated ? `genie-flow-node genie-stagger-in${expanded === index ? " genie-flow-node-active" : ""}` : undefined}
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: 4,
                width: 148,
                padding: "10px 8px",
                borderRadius: 10,
                border: expanded === index ? "1px solid #2f83e0" : "1px solid #232a33",
                backgroundColor: expanded === index ? "#182534" : "#161c24",
                color: "#e6e9ee",
                cursor: "pointer",
                textAlign: "center",
                fontFamily: "inherit",
                animationDelay: animated ? `${index * 90}ms` : undefined,
              }}
            >
              <span style={{ fontSize: 22 }} aria-hidden="true">
                {iconForComponent(section.title)}
              </span>
              <Text size={200} weight="semibold" style={{ lineHeight: 1.2 }}>
                {section.title}
              </Text>
            </button>
            {index < sections.length - 1 ? (
              animated ? (
                <div style={{ position: "relative", width: 36, height: 20, flexShrink: 0 }}>
                  <div className="genie-flow-connector genie-flow-connector-active" />
                  <div className="genie-flow-particle" style={{ animationDelay: `${index * 220}ms` }} />
                </div>
              ) : (
                <Text size={400} style={{ opacity: 0.35 }} aria-hidden="true">
                  →
                </Text>
              )
            ) : null}
          </div>
        ))}
      </div>

      {expanded !== null && sections[expanded] ? (
        <div
          className="genie-fade-in"
          style={{
            borderLeft: "3px solid #2f83e0",
            paddingLeft: 12,
            marginBottom: 4,
          }}
        >
          <Text weight="semibold" size={300} style={{ display: "block", marginBottom: 4 }}>
            {iconForComponent(sections[expanded].title)} {sections[expanded].title}
          </Text>
          <Text size={300} style={{ whiteSpace: "pre-wrap", opacity: 0.85 }}>
            {sections[expanded].body || "(no additional detail provided)"}
          </Text>
        </div>
      ) : null}
    </div>
  );
}
