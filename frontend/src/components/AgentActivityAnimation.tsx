import { Text } from "@fluentui/react-components";
import { describeEvent } from "@/utils/workflowEventText";
import type { WorkflowStreamEvent } from "@/types/workflowEvents";

/**
 * A prominent, animated "Genie is working with your agents..." banner shown
 * in place of a page's real content while the specific agent output that
 * page needs hasn't arrived yet. Distinct from the compact, persistent
 * `LiveWorkflowPulse` thin status line - this is a dedicated waiting state
 * the caller renders only until its own data shows up, at which point the
 * page swaps this out for the real detail (the swap itself is just the
 * caller's existing conditional render - this component owns no polling or
 * state of its own).
 */
export function AgentActivityAnimation({
  label,
  events = [],
}: {
  label: string;
  events?: WorkflowStreamEvent[];
}): JSX.Element {
  const lastEvent = events[events.length - 1];

  return (
    <div
      className="genie-fade-in genie-agent-activity"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 16,
        padding: "20px 24px",
        borderRadius: 12,
        border: "1px solid #232a33",
        backgroundColor: "rgba(19, 25, 33, 0.55)",
        marginBottom: 16,
      }}
    >
      <span className="genie-sparkle" style={{ fontSize: 32 }} aria-hidden="true">
        🧞
      </span>
      <div style={{ flex: 1 }}>
        <Text weight="semibold" size={300} style={{ display: "block", marginBottom: 6 }}>
          {label}
        </Text>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span className="genie-bounce-dots" aria-hidden="true">
            <span className="genie-bounce-dot" />
            <span className="genie-bounce-dot" />
            <span className="genie-bounce-dot" />
          </span>
          <Text size={200} style={{ opacity: 0.7 }}>
            {lastEvent ? describeEvent(lastEvent) : "Coordinating specialist agents in real time..."}
          </Text>
        </div>
      </div>
    </div>
  );
}
