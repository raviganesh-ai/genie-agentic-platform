import type { GovernanceEvent } from "@/types/governance";
import type { WorkflowRunResult } from "@/types/workflow";
import type { MemoryUpdateSummary } from "@/types/missionControl";

/**
 * Executive-grade "contribution score" for one agent, computed ONLY from
 * real, already-returned counts (completed steps, memory writes,
 * recommendations produced, approvals requested) - never a hidden/invented
 * business value, per the explicit instruction to calculate contribution
 * scores solely from available non-sensitive fields.
 */
export function computeAgentContributionScore(params: {
  agentId: string;
  latestRun: WorkflowRunResult | null;
  memoryUpdates: MemoryUpdateSummary[];
  governanceEvents: GovernanceEvent[];
}): number {
  const { agentId, latestRun, memoryUpdates, governanceEvents } = params;

  const completedSteps =
    latestRun?.step_results.filter((r) => r.agent_id === agentId && r.status === "completed")
      .length ?? 0;
  const memoryWrites = memoryUpdates.filter((m) => m.agent_id === agentId).length;
  const executionEvents = governanceEvents.filter(
    (e) => e.agent_id === agentId && e.category === "agent_execution",
  ).length;

  return completedSteps * 10 + memoryWrites * 5 + executionEvents * 2;
}

export type AchievementId =
  | "first_recommendation"
  | "collaboration_champion"
  | "governance_clean_run"
  | "memory_contributor"
  | "approval_ready";

export interface Achievement {
  id: AchievementId;
  label: string;
  earned: boolean;
}

/** Achievement badges derived from real counts - purely a UI framing layer. */
export function computeAgentAchievements(params: {
  completedSteps: number;
  memoryWrites: number;
  handoffCount: number;
  hasFailedGovernanceEvent: boolean;
}): Achievement[] {
  const { completedSteps, memoryWrites, handoffCount, hasFailedGovernanceEvent } = params;
  return [
    {
      id: "first_recommendation",
      label: "First Recommendation",
      earned: completedSteps >= 1,
    },
    {
      id: "collaboration_champion",
      label: "Collaboration Champion",
      earned: handoffCount >= 3,
    },
    {
      id: "governance_clean_run",
      label: "Governance Clean Run",
      earned: completedSteps > 0 && !hasFailedGovernanceEvent,
    },
    {
      id: "memory_contributor",
      label: "Memory Contributor",
      earned: memoryWrites >= 3,
    },
    {
      id: "approval_ready",
      label: "Approval Ready",
      earned: completedSteps >= 5,
    },
  ];
}
