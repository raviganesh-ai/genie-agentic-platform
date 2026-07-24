import { apiFetch } from "./httpClient";
import type { RecommendationTraceResponse, SessionReplayResponse } from "@/types/replay";

export const replayApi = {
  getSessionReplay(sessionId: string): Promise<SessionReplayResponse> {
    return apiFetch<SessionReplayResponse>(`/sessions/${sessionId}/replay`);
  },
  getRecommendationTrace(
    sessionId: string,
    recommendationId: string,
  ): Promise<RecommendationTraceResponse> {
    return apiFetch<RecommendationTraceResponse>(
      `/sessions/${sessionId}/recommendations/${recommendationId}/trace`,
    );
  },
};
