import { apiFetch } from "./httpClient";
import type { DiscoveryCase, DiscoveryQaMode } from "@/types/discovery";

const path = (sessionId: string, suffix = "") =>
  `/sessions/${sessionId}/discovery${suffix}`;

export const discoveryApi = {
  list(): Promise<DiscoveryCase[]> {
    return apiFetch<DiscoveryCase[]>("/discovery");
  },
  get(sessionId: string): Promise<DiscoveryCase> {
    return apiFetch<DiscoveryCase>(path(sessionId));
  },
  createOrResume(
    sessionId: string,
    sourceUploadIds: string[],
    modelDeploymentRef?: string,
    saveEnabled = false,
  ): Promise<DiscoveryCase> {
    return apiFetch<DiscoveryCase>(path(sessionId), {
      method: "POST",
      body: {
        source_upload_ids: sourceUploadIds,
        model_deployment_ref: modelDeploymentRef,
        save_enabled: saveEnabled,
      },
    });
  },
  setSavePreference(sessionId: string, enabled: boolean): Promise<DiscoveryCase> {
    return apiFetch<DiscoveryCase>(path(sessionId, "/save-preference"), {
      method: "PUT",
      body: { enabled },
    });
  },
  analyze(sessionId: string): Promise<DiscoveryCase> {
    return apiFetch<DiscoveryCase>(path(sessionId, "/analyze"), { method: "POST" });
  },
  selectPersona(sessionId: string, personaId: string): Promise<DiscoveryCase> {
    return this.selectPersonas(sessionId, [personaId]);
  },
  selectPersonas(sessionId: string, personaIds: string[]): Promise<DiscoveryCase> {
    return apiFetch<DiscoveryCase>(path(sessionId, "/persona"), {
      method: "POST",
      body: { persona_ids: personaIds },
    });
  },
  setQaMode(sessionId: string, mode: DiscoveryQaMode): Promise<DiscoveryCase> {
    return apiFetch<DiscoveryCase>(path(sessionId, "/qa-mode"), {
      method: "POST",
      body: { mode },
    });
  },
  answerQuestion(
    sessionId: string,
    questionId: string,
    answer: string | null,
  ): Promise<DiscoveryCase> {
    return apiFetch<DiscoveryCase>(path(sessionId, `/questions/${questionId}/answer`), {
      method: "POST",
      body: { answer },
    });
  },
  respondToRecommendation(
    sessionId: string,
    questionId: string,
    accepted: boolean,
  ): Promise<DiscoveryCase> {
    return apiFetch<DiscoveryCase>(path(sessionId, `/questions/${questionId}/recommendation`), {
      method: "POST",
      body: { accepted },
    });
  },
  generateSolutions(sessionId: string): Promise<DiscoveryCase> {
    return apiFetch<DiscoveryCase>(path(sessionId, "/solutions"), { method: "POST" });
  },
  refreshSolutionPricing(sessionId: string): Promise<DiscoveryCase> {
    return apiFetch<DiscoveryCase>(path(sessionId, "/solutions/pricing"), { method: "POST" });
  },
  selectSolution(sessionId: string, solutionId: string): Promise<DiscoveryCase> {
    return apiFetch<DiscoveryCase>(path(sessionId, "/solution"), {
      method: "POST",
      body: { solution_id: solutionId },
    });
  },
  startPrototype(sessionId: string): Promise<DiscoveryCase> {
    return apiFetch<DiscoveryCase>(path(sessionId, "/prototype"), { method: "POST" });
  },
  delete(sessionId: string): Promise<void> {
    return apiFetch<void>(path(sessionId), { method: "DELETE" });
  },
};