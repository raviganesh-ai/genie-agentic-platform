import { Avatar, Text } from "@fluentui/react-components";
import { SectionCard } from "@/components/SectionCard";
import { agentHealthPalette } from "@/styles/theme";
import type { MissionControlSnapshot } from "@/types/missionControl";

function ribbonEntry(agentId: string, color: string): JSX.Element {
  return (
    <div key={agentId} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
      <Avatar name={agentId} color="colorful" style={{ border: `2px solid ${color}` }} />
      <Text size={100} style={{ maxWidth: 72, textAlign: "center", overflow: "hidden", textOverflow: "ellipsis" }}>
        {agentId}
      </Text>
    </div>
  );
}

export function ActiveAgentRibbon({
  snapshot,
}: {
  snapshot: MissionControlSnapshot;
}): JSX.Element {
  return (
    <SectionCard title="Active Agents">
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
        {snapshot.active_agents.map((id) => ribbonEntry(id, agentHealthPalette.analyzing))}
        {snapshot.blocked_agents.map((id) => ribbonEntry(id, agentHealthPalette.blocked))}
        {snapshot.completed_agents.map((id) => ribbonEntry(id, agentHealthPalette.completed))}
        {snapshot.active_agents.length === 0 &&
        snapshot.blocked_agents.length === 0 &&
        snapshot.completed_agents.length === 0 ? (
          <Text size={300} style={{ opacity: 0.7 }}>
            No agents active yet.
          </Text>
        ) : null}
      </div>
    </SectionCard>
  );
}
