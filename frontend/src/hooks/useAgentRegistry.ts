import { useCallback } from "react";
import { agentApi } from "@/services/agentApi";
import { useAsyncResource, type AsyncResourceState } from "./useAsyncResource";
import type { AgentSummary } from "@/types/agents";

/**
 * The registered agent catalog is global (not session-scoped), so this
 * fetches once per page mount with no polling - just enough for the
 * Agentic Workflow diagram to look up real names/descriptions/connections.
 */
export function useAgentRegistry(): AsyncResourceState<AgentSummary[]> {
  const fetcher = useCallback(() => agentApi.list(), []);
  return useAsyncResource(fetcher, []);
}
