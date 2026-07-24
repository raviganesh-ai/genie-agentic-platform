import { apiFetch } from "./httpClient";
import type { MissionControlSnapshot, TimelineEntry } from "@/types/missionControl";
import type { ApprovalRequest } from "@/types/governance";
import type { WorkflowStatus } from "@/types/workflow";

export interface MissionControlProgress {
  workflow_run_id: string | null;
  workflow_status: WorkflowStatus | null;
  mission_progress: number;
  current_workflow_step: string | null;
}

export interface MissionControlActiveAgents {
  active_agents: string[];
  completed_agents: string[];
  blocked_agents: string[];
}

export const missionControlApi = {
  getSnapshot(sessionId: string): Promise<MissionControlSnapshot> {
    return apiFetch<MissionControlSnapshot>(`/sessions/${sessionId}/mission-control`);
  },
  getProgress(sessionId: string): Promise<MissionControlProgress> {
    return apiFetch<MissionControlProgress>(`/sessions/${sessionId}/mission-control/progress`);
  },
  getActiveAgents(sessionId: string): Promise<MissionControlActiveAgents> {
    return apiFetch<MissionControlActiveAgents>(
      `/sessions/${sessionId}/mission-control/active-agents`,
    );
  },
  getTimeline(sessionId: string): Promise<TimelineEntry[]> {
    return apiFetch<TimelineEntry[]>(`/sessions/${sessionId}/mission-control/timeline`);
  },
  getApprovalStatus(sessionId: string): Promise<ApprovalRequest[]> {
    return apiFetch<ApprovalRequest[]>(`/sessions/${sessionId}/mission-control/approval-status`);
  },
};
