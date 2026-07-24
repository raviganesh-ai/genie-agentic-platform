import { Text } from "@fluentui/react-components";
import { SectionCard } from "@/components/SectionCard";
import type { TimelineEntry } from "@/types/missionControl";

const KIND_LABELS: Record<TimelineEntry["kind"], string> = {
  workflow_step: "Workflow Step",
  handoff: "Agent Handoff",
  approval: "Approval",
  collaboration: "Collaboration",
};

export function LiveActivityFeed({ timeline }: { timeline: TimelineEntry[] }): JSX.Element {
  const sorted = [...timeline].sort((a, b) => b.timestamp.localeCompare(a.timestamp));
  return (
    <SectionCard title="Live Activity Feed">
      <div style={{ display: "flex", flexDirection: "column", gap: 8, maxHeight: 320, overflowY: "auto" }}>
        {sorted.length === 0 ? (
          <Text size={300} style={{ opacity: 0.7 }}>
            No activity yet.
          </Text>
        ) : (
          sorted.map((entry, index) => (
            <div
              key={`${entry.timestamp}-${index}`}
              style={{ borderLeft: "2px solid #2f83e0", paddingLeft: 10 }}
            >
              <Text size={200} style={{ opacity: 0.6, display: "block" }}>
                {new Date(entry.timestamp).toLocaleTimeString()} · {KIND_LABELS[entry.kind]}
                {entry.agent_id ? ` · ${entry.agent_id}` : ""}
              </Text>
              <Text size={300}>{entry.label}</Text>
            </div>
          ))
        )}
      </div>
    </SectionCard>
  );
}
