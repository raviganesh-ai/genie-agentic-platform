import { Badge } from "@fluentui/react-components";
import { statusPalette } from "@/styles/theme";
import type { GovernanceComplianceState } from "@/types/governance";

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
