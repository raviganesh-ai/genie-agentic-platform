import { apiFetch } from "./httpClient";
import type { ApprovalDecision, ApprovalDecisionOutcome, ApprovalRequest } from "@/types/governance";

export const approvalApi = {
  list(sessionId: string): Promise<ApprovalRequest[]> {
    return apiFetch<ApprovalRequest[]>(`/sessions/${sessionId}/approvals`);
  },
  /**
   * `workflowRunId` is required when deciding the final-output-approval
   * checkpoint (the deploy gate) - the backend uses it to look up the
   * Governance Reviewer's ("Peer Reviewer") gate verdict for that run and
   * fails closed (409) unless every gate passed or a risk acceptance was
   * already recorded for it. It is ignored for every other checkpoint.
   */
  decide(
    sessionId: string,
    requestId: string,
    decision: ApprovalDecisionOutcome,
    rationale = "",
    workflowRunId?: string,
  ): Promise<ApprovalDecision> {
    return apiFetch<ApprovalDecision>(`/sessions/${sessionId}/approvals/${requestId}/decide`, {
      method: "POST",
      body: { decision, rationale, workflow_run_id: workflowRunId ?? null },
    });
  },
};
