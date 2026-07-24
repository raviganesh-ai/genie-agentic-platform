import { useCallback, useMemo } from "react";
import { agentApi } from "@/services/agentApi";
import { missionControlApi } from "@/services/missionControlApi";
import { workflowApi } from "@/services/workflowApi";
import { governanceApi } from "@/services/governanceApi";
import { approvalApi } from "@/services/approvalApi";
import { deriveAgentActivityStatus } from "@/services/adapters/agentStatus";
import {
  computeAgentAchievements,
  computeAgentContributionScore,
  type Achievement,
} from "@/services/adapters/contributionScore";
import { useAsyncResource, type AsyncResourceState } from "./useAsyncResource";
import type { AgentActivityStatus, AgentDefinition } from "@/types/agent";

export interface AgentArenaCard {
  agent: AgentDefinition;
  status: AgentActivityStatus;
  contributionScore: number;
  achievements: Achievement[];
}

interface AgentArenaData {
  cards: AgentArenaCard[];
}

const DEFAULT_POLL_MS = Number(import.meta.env.VITE_AGENT_ARENA_POLL_MS ?? 0);

export function useAgentArena(
  sessionId: string | null,
  pollIntervalMs: number = DEFAULT_POLL_MS,
): AsyncResourceState<AgentArenaData> {
  const fetcher = useCallback(async (): Promise<AgentArenaData> => {
    if (!sessionId) return { cards: [] };

    const [agents, activeAgents, snapshot, runs, approvals, governanceEvents] = await Promise.all([
      agentApi.list(),
      missionControlApi.getActiveAgents(sessionId),
      missionControlApi.getSnapshot(sessionId),
      workflowApi.listRuns(sessionId),
      approvalApi.list(sessionId),
      governanceApi.listEvents(sessionId),
    ]);

    const latestRun = runs.length > 0 ? runs[runs.length - 1] : null;

    const cards: AgentArenaCard[] = agents
      .filter((agent) => agent.enabled)
      .map((agent) => {
        const status = deriveAgentActivityStatus({
          agentId: agent.id,
          activeAgents: activeAgents.active_agents,
          completedAgents: activeAgents.completed_agents,
          blockedAgents: activeAgents.blocked_agents,
          latestRun,
          pendingApprovals: approvals,
        });

        const completedSteps =
          latestRun?.step_results.filter(
            (r) => r.agent_id === agent.id && r.status === "completed",
          ).length ?? 0;
        const memoryWrites = snapshot.memory_updates.filter(
          (m) => m.agent_id === agent.id,
        ).length;
        const handoffCount = snapshot.handoffs.filter(
          (h) => h.source_agent_id === agent.id || h.target_agent_id === agent.id,
        ).length;
        const hasFailedGovernanceEvent = governanceEvents.some(
          (e) => e.agent_id === agent.id && e.category === "access_denied",
        );

        return {
          agent,
          status,
          contributionScore: computeAgentContributionScore({
            agentId: agent.id,
            latestRun,
            memoryUpdates: snapshot.memory_updates,
            governanceEvents,
          }),
          achievements: computeAgentAchievements({
            completedSteps,
            memoryWrites,
            handoffCount,
            hasFailedGovernanceEvent,
          }),
        };
      });

    return { cards };
  }, [sessionId]);

  const result = useAsyncResource(fetcher, [sessionId], {
    enabled: Boolean(sessionId),
    pollIntervalMs,
  });

  const cards = useMemo(() => result.data?.cards ?? [], [result.data]);

  return { ...result, data: result.data ? { cards } : null };
}
