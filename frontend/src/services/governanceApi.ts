import { apiFetch } from "./httpClient";
import type { GovernanceEvent, GovernanceGateReport } from "@/types/governance";
import type { WorkflowRunResult } from "@/types/workflow";

export const governanceApi = {
  listEvents(sessionId: string): Promise<GovernanceEvent[]> {
    return apiFetch<GovernanceEvent[]>(`/sessions/${sessionId}/governance/events`);
  },
  /**
   * Records a Responsible AI Accountability checkpoint: a person explicitly
   * proceeding the Discovery Wizard past a workflow stage. The backend
   * always attributes this to the authenticated caller server-side - no
   * client-supplied "confirmed by" field exists.
   */
  confirmCheckpoint(
    sessionId: string,
    traceId: string,
    stageKey: string,
    stageLabel: string,
  ): Promise<GovernanceEvent> {
    return apiFetch<GovernanceEvent>(`/sessions/${sessionId}/governance/checkpoints/confirm`, {
      method: "POST",
      body: { trace_id: traceId, stage_key: stageKey, stage_label: stageLabel },
    });
  },
  /** The Governance Reviewer's ("Peer Reviewer") consolidated 4-gate verdict for this run. */
  getGateReport(sessionId: string, workflowRunId: string): Promise<GovernanceGateReport> {
    return apiFetch<GovernanceGateReport>(
      `/sessions/${sessionId}/governance/${workflowRunId}/gate-report`,
    );
  },
  /**
   * Regenerates the build to resolve the selected findings and re-runs every
   * Peer Review gate step against the regenerated build.
   */
  applyFixes(
    sessionId: string,
    workflowRunId: string,
    traceId: string,
    selectedFindings: string[],
  ): Promise<WorkflowRunResult> {
    return apiFetch<WorkflowRunResult>(`/sessions/${sessionId}/governance/${workflowRunId}/fixes`, {
      method: "POST",
      body: { trace_id: traceId, selected_findings: selectedFindings },
    });
  },
  /**
   * Records a human's explicit, justified acceptance of residual Peer Review
   * risk so the deploy gate can be approved despite a blocked verdict. The
   * backend always attributes this to the authenticated caller server-side.
   */
  submitRiskAcceptance(
    sessionId: string,
    workflowRunId: string,
    traceId: string,
    justification: string,
    acceptedFindingIds: string[],
  ): Promise<GovernanceEvent> {
    return apiFetch<GovernanceEvent>(
      `/sessions/${sessionId}/governance/${workflowRunId}/risk-acceptance`,
      {
        method: "POST",
        body: {
          trace_id: traceId,
          justification,
          accepted_finding_ids: acceptedFindingIds,
        },
      },
    );
  },
};
