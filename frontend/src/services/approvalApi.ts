import { apiFetch } from "./httpClient";
import type { ApprovalDecision, ApprovalDecisionOutcome, ApprovalRequest } from "@/types/governance";

export const approvalApi = {
  list(sessionId: string): Promise<ApprovalRequest[]> {
    return apiFetch<ApprovalRequest[]>(`/sessions/${sessionId}/approvals`);
  },
  decide(
    sessionId: string,
    requestId: string,
    decision: ApprovalDecisionOutcome,
    rationale = "",
  ): Promise<ApprovalDecision> {
    return apiFetch<ApprovalDecision>(`/sessions/${sessionId}/approvals/${requestId}/decide`, {
      method: "POST",
      body: { decision, rationale },
    });
  },
};
