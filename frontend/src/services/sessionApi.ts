import { apiFetch } from "./httpClient";
import type { Session } from "@/types/session";
import type { WorkflowRunResult } from "@/types/workflow";

export const sessionApi = {
  create(title: string): Promise<Session> {
    return apiFetch<Session>("/sessions", { method: "POST", body: { title } });
  },
  list(): Promise<Session[]> {
    return apiFetch<Session[]>("/sessions");
  },
  get(sessionId: string): Promise<Session> {
    return apiFetch<Session>(`/sessions/${sessionId}`);
  },
  resume(sessionId: string): Promise<WorkflowRunResult> {
    return apiFetch<WorkflowRunResult>(`/sessions/${sessionId}/resume`, { method: "POST" });
  },
};
