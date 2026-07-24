import { apiFetch } from "./httpClient";
import type { GovernanceEvent } from "@/types/governance";

export const governanceApi = {
  listEvents(sessionId: string): Promise<GovernanceEvent[]> {
    return apiFetch<GovernanceEvent[]>(`/sessions/${sessionId}/governance/events`);
  },
};
