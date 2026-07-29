import { Text } from "@fluentui/react-components";

export interface UiScreenCard {
  title: string;
  description: string;
}

interface UiScreenShowcaseProps {
  screens: UiScreenCard[];
  emptyLabel?: string;
}

/** Picks a contextual icon per screen from common keywords in its name -
 * purely a display affordance, never changes what's rendered. Falls back
 * to a generic screen icon when nothing matches. */
const SCREEN_ICONS: Array<[RegExp, string]> = [
  [/kick[\s-]?off|start|intake|upload|launch|create|new/i, "🚀"],
  [/progress|status|live|monitor|track/i, "📊"],
  [/result|output|review|download|summary|report/i, "✅"],
  [/chat|message|talk|converse/i, "💬"],
  [/approve|approval|governance|policy/i, "🛡️"],
  [/setting|config|preference/i, "⚙️"],
];

function iconForScreen(title: string): string {
  for (const [regex, icon] of SCREEN_ICONS) {
    if (regex.test(title)) return icon;
  }
  return "🖥️";
}

/** Cycled purely for visual variety between adjacent screen cards - not
 * tied to any agent/role semantics, unlike the Multi-Agent Workflow
 * section's own colour coding. */
const ACCENTS = ["#2f83e0", "#3fa66a", "#c9a227", "#a374db"];

/**
 * Shows this mission's UI Design as a gallery of screen "mockup" cards
 * connected in the order the user moves through them - kick-off,
 * progress, output, etc. - with nothing about the Multi-Agent Workflow
 * (no Orchestrator Agent node, no agent hand-offs) so this section reads
 * purely as "what the customer-facing UI looks like" instead of an
 * agent-flow diagram wearing a UI costume.
 */
export function UiScreenShowcase({ screens, emptyLabel }: UiScreenShowcaseProps): JSX.Element {
  if (screens.length === 0) {
    return (
      <Text size={300} style={{ opacity: 0.7 }}>
        {emptyLabel ?? "No UI screens described yet."}
      </Text>
    );
  }

  return (
    <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", rowGap: 20 }}>
      {screens.map((screen, index) => {
        const accent = ACCENTS[index % ACCENTS.length];
        return (
          <div key={screen.title} style={{ display: "flex", alignItems: "center" }}>
            <div
              className="genie-fade-in genie-ui-screen-card"
              tabIndex={0}
              style={{
                width: 224,
                borderRadius: 12,
                overflow: "hidden",
                border: "1px solid #232a33",
                backgroundColor: "#12181f",
                boxShadow: "0 2px 10px rgba(0, 0, 0, 0.35)",
              }}
            >
              <div style={{ height: 8, background: `linear-gradient(90deg, ${accent}, ${accent}55)` }} />
              <div style={{ padding: "14px 14px 16px" }}>
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    marginBottom: 10,
                  }}
                >
                  <span
                    style={{
                      fontSize: 10,
                      fontWeight: 600,
                      letterSpacing: 0.6,
                      textTransform: "uppercase",
                      opacity: 0.55,
                    }}
                  >
                    Screen {index + 1} of {screens.length}
                  </span>
                  <span style={{ fontSize: 22 }} aria-hidden="true">
                    {iconForScreen(screen.title)}
                  </span>
                </div>
                <Text weight="semibold" size={300} style={{ display: "block", marginBottom: 6 }}>
                  {screen.title}
                </Text>
                <Text size={200} style={{ opacity: 0.75, lineHeight: 1.4 }}>
                  {screen.description}
                </Text>
              </div>
            </div>
            {index < screens.length - 1 ? (
              <div
                aria-hidden="true"
                style={{
                  width: 28,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: 18,
                  opacity: 0.4,
                  flexShrink: 0,
                }}
              >
                →
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
