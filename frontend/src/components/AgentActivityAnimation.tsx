import { useEffect, useState } from "react";
import { Text } from "@fluentui/react-components";
import { describeEvent } from "@/utils/workflowEventText";
import type { WorkflowStreamEvent } from "@/types/workflowEvents";

/** Formats a millisecond duration as e.g. "3s", "1m 42s", "18m 03s". */
function formatElapsed(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes === 0) return `${seconds}s`;
  return `${minutes}m ${seconds.toString().padStart(2, "0")}s`;
}

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
  startedAt,
}: {
  label: string;
  events?: WorkflowStreamEvent[];
  /** ISO timestamp this activity began - when provided, a ticking "Xm Ys"
   *  elapsed counter is shown so a genuinely long-running phase never looks
   *  indistinguishable from a frozen page. */
  startedAt?: string | null;
}): JSX.Element {
  const lastEvent = events[events.length - 1];

  // Ticks once a second purely to force a re-render so the elapsed counter
  // below stays live - no other state derives from `now` itself.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!startedAt) return undefined;
    const interval = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(interval);
  }, [startedAt]);
  const elapsedLabel = startedAt ? formatElapsed(now - Date.parse(startedAt)) : null;

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
        <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 6 }}>
          <Text weight="semibold" size={300} style={{ display: "block" }}>
            {label}
          </Text>
          {elapsedLabel ? (
            <Text size={200} style={{ opacity: 0.55 }}>
              ({elapsedLabel})
            </Text>
          ) : null}
        </div>
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
