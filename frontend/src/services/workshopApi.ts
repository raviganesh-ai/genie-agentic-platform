import { apiFetch } from "./httpClient";
import type { WorkflowRunResult } from "@/types/workflow";
import type { ReanalysisRequestType, ReanalysisResult } from "@/types/reanalysis";

interface ReanalysisActionBody {
  workflow_run_id: string;
  trace_id: string;
  target_recommendation_id?: string | null;
  rationale?: string;
}

export const workshopApi = {
  chatWithAllAgents(
    sessionId: string,
    workflowRunId: string,
    message: string,
    traceId?: string,
  ): Promise<WorkflowRunResult> {
    return apiFetch<WorkflowRunResult>(`/sessions/${sessionId}/workshop/chat`, {
      method: "POST",
      body: { workflow_run_id: workflowRunId, message, trace_id: traceId },
    });
  },
  chatWithAgent(
    sessionId: string,
    agentId: string,
    workflowRunId: string,
    message: string,
    traceId?: string,
  ): Promise<WorkflowRunResult> {
    return apiFetch<WorkflowRunResult>(`/sessions/${sessionId}/workshop/chat/${agentId}`, {
      method: "POST",
      body: { workflow_run_id: workflowRunId, message, trace_id: traceId },
    });
  },
  challengeRecommendation(
    sessionId: string,
    body: ReanalysisActionBody,
  ): Promise<ReanalysisResult> {
    return apiFetch<ReanalysisResult>(`/sessions/${sessionId}/workshop/challenge`, {
      method: "POST",
      body,
    });
  },
  requestAlternativeArchitecture(
    sessionId: string,
    body: ReanalysisActionBody,
  ): Promise<ReanalysisResult> {
    return apiFetch<ReanalysisResult>(`/sessions/${sessionId}/workshop/alternative`, {
      method: "POST",
      body,
    });
  },
  submitReanalysisRequest(
    sessionId: string,
    body: ReanalysisActionBody & { request_type: ReanalysisRequestType },
  ): Promise<ReanalysisResult> {
    return apiFetch<ReanalysisResult>(`/sessions/${sessionId}/workshop/reanalysis`, {
      method: "POST",
      body,
    });
  },
  updatePriorities(sessionId: string, body: ReanalysisActionBody): Promise<ReanalysisResult> {
    return apiFetch<ReanalysisResult>(`/sessions/${sessionId}/workshop/priorities`, {
      method: "POST",
      body,
    });
  },
  regenerateComponent(
    sessionId: string,
    body: { component_label: string; existing_code: string; instructions: string; trace_id?: string },
  ): Promise<{ component_label: string; code: string }> {
    return apiFetch<{ component_label: string; code: string }>(
      `/sessions/${sessionId}/workshop/regenerate-component`,
      {
        method: "POST",
        body,
      },
    );
  },
  getBuildComponents(
    sessionId: string,
    workflowRunId: string,
  ): Promise<{ build_output: string }> {
    return apiFetch<{ build_output: string }>(
      `/sessions/${sessionId}/workshop/build-components/${workflowRunId}`,
      { method: "GET" },
    );
  },
};
