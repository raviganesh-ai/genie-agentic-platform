import { useCallback } from "react";
import { replayApi } from "@/services/replayApi";
import { buildReplayTimeline } from "@/services/adapters/replayTimeline";
import { useAsyncResource, type AsyncResourceState } from "./useAsyncResource";
import type { ReplayTimelineEntry, SessionReplayResponse } from "@/types/replay";

export interface ReplayData {
  replay: SessionReplayResponse;
  timeline: ReplayTimelineEntry[];
}

const DEFAULT_POLL_MS = Number(import.meta.env.VITE_REPLAY_POLL_MS ?? 0);

export function useReplay(
  sessionId: string | null,
  pollIntervalMs: number = DEFAULT_POLL_MS,
): AsyncResourceState<ReplayData> {
  const fetcher = useCallback(async (): Promise<ReplayData> => {
    if (!sessionId) throw new Error("No active session");
    const replay = await replayApi.getSessionReplay(sessionId);
    return { replay, timeline: buildReplayTimeline(replay) };
  }, [sessionId]);

  return useAsyncResource(fetcher, [sessionId], {
    enabled: Boolean(sessionId),
    pollIntervalMs,
  });
}
