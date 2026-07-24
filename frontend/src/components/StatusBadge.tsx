import { Badge } from "@fluentui/react-components";
import { agentHealthPalette, statusPalette } from "@/styles/theme";
import type { AgentActivityStatus } from "@/types/agent";
import type { GovernanceComplianceState } from "@/types/governance";

const AGENT_STATUS_LABELS: Record<AgentActivityStatus, string> = {
  idle: "Idle",
  analyzing: "Analyzing",
  collaborating: "Collaborating",
  waiting_for_approval: "Waiting for Approval",
  generating_output: "Generating Output",
  completed: "Completed",
  blocked: "Blocked",
  failed: "Failed",
};

export function AgentStatusBadge({ status }: { status: AgentActivityStatus }): JSX.Element {
  return (
    <Badge
      shape="rounded"
      style={{ backgroundColor: agentHealthPalette[status], color: "#0b0f14" }}
    >
      {AGENT_STATUS_LABELS[status]}
    </Badge>
  );
}

const COMPLIANCE_LABELS: Record<GovernanceComplianceState, string> = {
  compliant: "Compliant",
  warning: "Attention Required",
  blocked: "Blocked",
  incomplete: "Incomplete",
  failed: "Failed",
};

export function GovernanceStatusBadge({
  state,
}: {
  state: GovernanceComplianceState;
}): JSX.Element {
  return (
    <Badge shape="rounded" style={{ backgroundColor: statusPalette[state], color: "#0b0f14" }}>
      {COMPLIANCE_LABELS[state]}
    </Badge>
  );
}
