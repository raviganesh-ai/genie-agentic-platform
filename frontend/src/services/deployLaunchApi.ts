import { apiFetch, downloadBinary } from "./httpClient";
import type { DeploymentPipelineRun } from "@/types/deployLaunch";

export const deployLaunchApi = {
  /**
   * Starts (or re-attempts) the real, 9-step Deploy & Launch pipeline for
   * this workflow run. Gated server-side on the `final-output-approval`
   * checkpoint - the backend auto-requests that checkpoint the first time
   * this is called with no existing request, and raises a 409 while it is
   * still pending (see `DeploymentPipelineService.start`).
   */
  start(sessionId: string, workflowRunId: string, traceId?: string): Promise<DeploymentPipelineRun> {
    return apiFetch<DeploymentPipelineRun>(`/sessions/${sessionId}/deploy-launch/start`, {
      method: "POST",
      body: { workflow_run_id: workflowRunId, trace_id: traceId ?? null },
    });
  },
  list(sessionId: string): Promise<DeploymentPipelineRun[]> {
    return apiFetch<DeploymentPipelineRun[]>(`/sessions/${sessionId}/deploy-launch/`);
  },
  get(sessionId: string, pipelineRunId: string): Promise<DeploymentPipelineRun> {
    return apiFetch<DeploymentPipelineRun>(`/sessions/${sessionId}/deploy-launch/${pipelineRunId}`);
  },
  /** Downloads a zip of the materialized backend build plus the generated least-access policy document. */
  async download(sessionId: string, pipelineRunId: string): Promise<{ blob: Blob; filename: string }> {
    return downloadBinary(`/sessions/${sessionId}/deploy-launch/${pipelineRunId}/download`);
  },
};
