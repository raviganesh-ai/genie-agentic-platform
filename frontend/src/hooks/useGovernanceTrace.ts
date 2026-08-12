import { useCallback, useMemo } from "react";
import { governanceApi } from "@/services/governanceApi";
import { approvalApi } from "@/services/approvalApi";
import { useAsyncResource, type AsyncResourceState } from "./useAsyncResource";
import type {
  ApprovalRequest,
  GovernanceComplianceState,
  GovernanceEvent,
} from "@/types/governance";

export interface GovernanceTraceData {
  events: GovernanceEvent[];
  approvals: ApprovalRequest[];
}

const DEFAULT_POLL_MS = Number(import.meta.env.VITE_GOVERNANCE_POLL_MS ?? 0);

/**
 * The authoritative "Overall status" for the Governance page, derived from
 * governance events and approvals only.
 */
export function deriveComplianceState(
  events: GovernanceEvent[],
  approvals: ApprovalRequest[],
): GovernanceComplianceState {
  if (events.some((e) => e.category === "access_denied")) return "blocked";
  if (approvals.some((a) => a.status === "rejected")) return "failed";
  if (approvals.some((a) => a.status === "expired")) return "incomplete";
  if (approvals.some((a) => a.status === "pending")) return "warning";
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

