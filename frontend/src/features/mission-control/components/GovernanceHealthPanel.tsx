import { Text } from "@fluentui/react-components";
import { SectionCard } from "@/components/SectionCard";
import { GovernanceStatusBadge } from "@/components/StatusBadge";
import type { MissionControlSnapshot } from "@/types/missionControl";

export function GovernanceHealthPanel({
  snapshot,
}: {
  snapshot: MissionControlSnapshot;
}): JSX.Element {
  const pendingApprovals = snapshot.approvals.filter((a) => a.status === "pending");
  return (
    <SectionCard title="Governance Health">
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
        <GovernanceStatusBadge
          state={snapshot.governance_status === "compliant" ? "compliant" : "warning"}
        />
        <Text size={300}>
          {pendingApprovals.length} pending approval{pendingApprovals.length === 1 ? "" : "s"}
        </Text>
      </div>
      <Text size={200} style={{ opacity: 0.7 }}>
        {snapshot.memory_updates.length} shared memory update
        {snapshot.memory_updates.length === 1 ? "" : "s"} recorded this session.
      </Text>
    </SectionCard>
  );
}
