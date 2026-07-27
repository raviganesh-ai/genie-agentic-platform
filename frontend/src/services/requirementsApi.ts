import { apiFetch } from "./httpClient";
import type { RequirementsQualification } from "@/types/requirementsQualification";

/**
 * Backs the Requirement Discovery Map's agentic-workflow qualification
 * banner against the backend's `/requirements` router
 * (backend/app/api/requirements.py).
 */
export const requirementsApi = {
  getQualification(
    sessionId: string,
    workflowRunId: string,
  ): Promise<RequirementsQualification> {
    return apiFetch<RequirementsQualification>(
      `/sessions/${sessionId}/requirements/${workflowRunId}/qualification`,
    );
  },
};
