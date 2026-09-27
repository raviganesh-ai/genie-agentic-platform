import { apiFetch } from "./httpClient";
import type { DeploymentPipelineRun } from "@/types/deployLaunch";

/**
 * Self-service inventory/cleanup for the current user's own prototypes,
 * across every session they own - lets a user who hits
 * `DeploymentPipelineService`'s per-owner active-prototype quota ("Active
 * prototype limit reached") delete a stale one themselves instead of
 * being stuck with no in-product recourse.
 */
export const prototypesApi = {
  listMine(): Promise<DeploymentPipelineRun[]> {
    return apiFetch<DeploymentPipelineRun[]>("/prototypes/mine");
  },
  deleteMine(pipelineRunId: string): Promise<void> {
    return apiFetch<void>(`/prototypes/mine/${pipelineRunId}`, { method: "DELETE" });
  },
};
