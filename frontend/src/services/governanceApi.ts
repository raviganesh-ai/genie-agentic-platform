import { apiFetch } from "./httpClient";
import type {
  AgentAssessmentsReport,
  GovernanceEvent,
  GovernanceGateReport,
} from "@/types/governance";
import type { WorkflowRunResult } from "@/types/workflow";

export const governanceApi = {
  listEvents(sessionId: string): Promise<GovernanceEvent[]> {
    return apiFetch<GovernanceEvent[]>(`/sessions/${sessionId}/peer-review/events`);
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
    return apiFetch<GovernanceEvent>(`/sessions/${sessionId}/peer-review/checkpoints/confirm`, {
      method: "POST",
      body: { trace_id: traceId, stage_key: stageKey, stage_label: stageLabel },
    });
  },
  /** The Peer Review Agent's consolidated 4-gate verdict for this run. */
  getGateReport(sessionId: string, workflowRunId: string): Promise<GovernanceGateReport> {
    return apiFetch<GovernanceGateReport>(
      `/sessions/${sessionId}/peer-review/${workflowRunId}/gate-report`,
    );
  },
  /**
   * Each specialist agent's own early, single-gate verdict (Security
   * Assessment Agent's security gate, Test Generation Agent's test-coverage
   * gate) - available as soon as that agent's own step completes, without
   * waiting for the slower, consolidated getGateReport verdict above.
   */
  getAgentAssessments(sessionId: string, workflowRunId: string): Promise<AgentAssessmentsReport> {
    return apiFetch<AgentAssessmentsReport>(
      `/sessions/${sessionId}/peer-review/${workflowRunId}/agent-assessments`,
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
    return apiFetch<WorkflowRunResult>(`/sessions/${sessionId}/peer-review/${workflowRunId}/fixes`, {
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
      `/sessions/${sessionId}/peer-review/${workflowRunId}/risk-acceptance`,
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
