import { apiFetch } from "./httpClient";
import type { WorkflowRunResult, WorkflowStepInput } from "@/types/workflow";

export const workflowApi = {
  run(sessionId: string, workflowId: string, traceId?: string): Promise<WorkflowRunResult> {
    return apiFetch<WorkflowRunResult>(`/sessions/${sessionId}/workflows/${workflowId}/run`, {
      method: "POST",
      body: traceId ? { trace_id: traceId } : undefined,
    });
  },
  resumeRun(
    sessionId: string,
    workflowRunId: string,
    traceId?: string,
    stepInputs?: Record<string, WorkflowStepInput>,
  ): Promise<WorkflowRunResult> {
    return apiFetch<WorkflowRunResult>(
      `/sessions/${sessionId}/workflows/runs/${workflowRunId}/resume`,
      {
        method: "POST",
        body:
          traceId || stepInputs
            ? { trace_id: traceId, step_inputs: stepInputs }
            : undefined,
      },
    );
  },
  getRun(sessionId: string, workflowRunId: string): Promise<WorkflowRunResult> {
    return apiFetch<WorkflowRunResult>(`/sessions/${sessionId}/workflows/runs/${workflowRunId}`);
  },
  listRuns(sessionId: string): Promise<WorkflowRunResult[]> {
    return apiFetch<WorkflowRunResult[]>(`/sessions/${sessionId}/workflows/runs`);
  },
};
