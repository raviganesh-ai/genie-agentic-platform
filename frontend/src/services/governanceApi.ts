import { apiFetch } from "./httpClient";
import type { GovernanceEvent } from "@/types/governance";

export const governanceApi = {
  listEvents(sessionId: string): Promise<GovernanceEvent[]> {
    return apiFetch<GovernanceEvent[]>(`/sessions/${sessionId}/governance/events`);
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
    return apiFetch<GovernanceEvent>(`/sessions/${sessionId}/governance/checkpoints/confirm`, {
      method: "POST",
      body: { trace_id: traceId, stage_key: stageKey, stage_label: stageLabel },
    });
  },
};
