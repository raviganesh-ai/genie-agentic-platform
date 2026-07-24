import { apiFetch } from "./httpClient";
import type { ArchitectureSnapshot } from "@/types/architecture";
import type { ReanalysisResult } from "@/types/reanalysis";

/**
 * Not one of the user-listed API service names, but required to back the
 * Architecture Studio page against the backend's actual `/architecture`
 * router (backend/app/api/architecture.py). Documented here rather than
 * invented client-side data.
 */
export const architectureApi = {
  get(sessionId: string, workflowRunId: string): Promise<ArchitectureSnapshot> {
    return apiFetch<ArchitectureSnapshot>(`/sessions/${sessionId}/architecture/${workflowRunId}`);
  },
  requestAlternative(
    sessionId: string,
    workflowRunId: string,
    traceId: string,
    rationale = "",
  ): Promise<ReanalysisResult> {
    return apiFetch<ReanalysisResult>(
      `/sessions/${sessionId}/architecture/${workflowRunId}/alternative`,
      { method: "POST", body: { trace_id: traceId, rationale } },
    );
  },
};
