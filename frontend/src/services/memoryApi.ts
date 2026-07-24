import { apiFetch } from "./httpClient";
import type { SharedMemoryRecord } from "@/types/memory";

export const memoryApi = {
  listShared(sessionId: string, traceId: string): Promise<SharedMemoryRecord[]> {
    return apiFetch<SharedMemoryRecord[]>(`/sessions/${sessionId}/memory/shared`, {
      query: { trace_id: traceId },
    });
  },
  getShared(sessionId: string, key: string, traceId: string): Promise<SharedMemoryRecord[]> {
    return apiFetch<SharedMemoryRecord[]>(`/sessions/${sessionId}/memory/shared/${key}`, {
      query: { trace_id: traceId },
    });
  },
};
