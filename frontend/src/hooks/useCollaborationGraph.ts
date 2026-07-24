import { useCallback } from "react";
import { missionControlApi } from "@/services/missionControlApi";
import { useAsyncResource, type AsyncResourceState } from "./useAsyncResource";
import type { DecisionGraph } from "@/types/collaboration";

const DEFAULT_POLL_MS = Number(import.meta.env.VITE_COLLABORATION_GRAPH_POLL_MS ?? 0);

/**
 * Backs the Collaboration Graph page. The decision graph is embedded in the
 * Mission Control snapshot (MissionControlSnapshot.decision_graph) - there
 * is no separate collaboration-graph endpoint on the backend.
 */
export function useCollaborationGraph(
  sessionId: string | null,
  pollIntervalMs: number = DEFAULT_POLL_MS,
): AsyncResourceState<DecisionGraph | null> {
  const fetcher = useCallback(async () => {
    if (!sessionId) return null;
    const snapshot = await missionControlApi.getSnapshot(sessionId);
    return snapshot.decision_graph;
  }, [sessionId]);

  return useAsyncResource(fetcher, [sessionId], {
    enabled: Boolean(sessionId),
    pollIntervalMs,
  });
}
