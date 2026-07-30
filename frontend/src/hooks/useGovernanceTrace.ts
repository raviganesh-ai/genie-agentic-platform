import { useCallback, useMemo } from "react";
import { governanceApi } from "@/services/governanceApi";
import { approvalApi } from "@/services/approvalApi";
import { useAsyncResource, type AsyncResourceState } from "./useAsyncResource";
import type {
  ApprovalRequest,
  GovernanceComplianceState,
  GovernanceEvent,
  GovernanceGateReport,
} from "@/types/governance";

export interface GovernanceTraceData {
  events: GovernanceEvent[];
  approvals: ApprovalRequest[];
}

const DEFAULT_POLL_MS = Number(import.meta.env.VITE_GOVERNANCE_POLL_MS ?? 0);

/**
 * The authoritative "Overall status" for the Governance page. Deliberately
 * takes the real ``GovernanceGateReport`` (the Governance Reviewer agent's
 * own consolidated verdict, fetched separately via
 * governanceApi.getGateReport) as an input rather than defaulting to
 * "compliant" whenever no explicit denial/rejection has happened *yet* -
 * "compliant" must only ever be reported once that agent has actually
 * returned a "reviewed" report with an "approved" decision. Before that
 * (report missing, "pending", or "undetermined"), the state is "pending",
 * even if every approval so far has been granted - approvals only cover
 * human sign-off on earlier steps, never the Governance Reviewer's own
 * real-time analysis of this run's code/tests/architecture.
 */
export function deriveComplianceState(
  events: GovernanceEvent[],
  approvals: ApprovalRequest[],
  gateReport: GovernanceGateReport | null | undefined,
  riskAccepted: boolean,
): GovernanceComplianceState {
  if (events.some((e) => e.category === "access_denied")) return "blocked";
  if (approvals.some((a) => a.status === "rejected")) return "failed";
  if (approvals.some((a) => a.status === "expired")) return "incomplete";
  if (approvals.some((a) => a.status === "pending")) return "warning";
  if (!gateReport || gateReport.status !== "reviewed") return "pending";
  if (gateReport.decision === "blocked" && !riskAccepted) return "blocked";
  return "compliant";
}

export function useGovernanceTrace(
  sessionId: string | null,
  pollIntervalMs: number = DEFAULT_POLL_MS,
): AsyncResourceState<GovernanceTraceData> {
  const fetcher = useCallback(async (): Promise<GovernanceTraceData> => {
    if (!sessionId) throw new Error("No active session");
    const [events, approvals] = await Promise.all([
      governanceApi.listEvents(sessionId),
      approvalApi.list(sessionId),
    ]);
    return { events, approvals };
  }, [sessionId]);

  const result = useAsyncResource(fetcher, [sessionId], {
    enabled: Boolean(sessionId),
    pollIntervalMs,
  });

  const data = useMemo(() => result.data, [result.data]);

  return { ...result, data };
}

