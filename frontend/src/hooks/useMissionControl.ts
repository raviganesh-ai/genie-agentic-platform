import { useCallback } from "react";
import { missionControlApi } from "@/services/missionControlApi";
import { useAsyncResource, type AsyncResourceState } from "./useAsyncResource";
import type { MissionControlSnapshot } from "@/types/missionControl";

const DEFAULT_POLL_MS = Number(import.meta.env.VITE_MISSION_CONTROL_POLL_MS ?? 0);

export function useMissionControl(
  sessionId: string | null,
  pollIntervalMs: number = DEFAULT_POLL_MS,
): AsyncResourceState<MissionControlSnapshot> {
  const fetcher = useCallback(() => {
    if (!sessionId) return Promise.reject(new Error("No active session"));
    return missionControlApi.getSnapshot(sessionId);
  }, [sessionId]);

  return useAsyncResource(fetcher, [sessionId], {
    enabled: Boolean(sessionId),
    pollIntervalMs,
  });
}
