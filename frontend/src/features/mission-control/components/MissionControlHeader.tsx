import { Text } from "@fluentui/react-components";
import { GovernanceStatusBadge } from "@/components/StatusBadge";
import type { MissionControlSnapshot } from "@/types/missionControl";

export function MissionControlHeader({
  snapshot,
}: {
  snapshot: MissionControlSnapshot;
}): JSX.Element {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
      <div>
        <Text weight="bold" size={600} style={{ display: "block" }}>
          Mission Control
        </Text>
        <Text size={300} style={{ opacity: 0.7 }}>
          Session {snapshot.session_id}
          {snapshot.workflow_run_id ? ` · Run ${snapshot.workflow_run_id}` : ""}
        </Text>
      </div>
      <GovernanceStatusBadge state={snapshot.governance_status === "compliant" ? "compliant" : "warning"} />
    </div>
  );
}
