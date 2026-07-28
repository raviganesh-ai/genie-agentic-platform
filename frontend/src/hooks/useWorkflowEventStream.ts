import { useEffect, useState } from "react";
import { openEventStream } from "@/services/httpClient";
import type { WorkflowStreamEvent } from "@/types/workflowEvents";

const RECONNECT_DELAY_MS = 3000;
const MAX_RECENT_EVENTS = 50;

export interface WorkflowEventStreamState {
  /** Most recent events received, oldest first (capped at MAX_RECENT_EVENTS). */
  events: WorkflowStreamEvent[];
  lastEvent: WorkflowStreamEvent | null;
  connected: boolean;
}

/**
 * Subscribes to the live `GET /sessions/{id}/workflow-events/stream` SSE
 * route for a session, so Mission Control pages can show each workflow
 * step's progress in near real time instead of waiting for the next poll.
 * Deliberately additive to (never a replacement for) each page's existing
 * `useAsyncResource`-based data fetching: this is just a live "is
 * currently happening" signal - callers should keep treating their normal
 * fetch as the source of truth and only use these events to know *when* to
 * refresh it sooner.
 *
 * Uses `fetch` + a manual `ReadableStream`/SSE-frame parser (via
 * `openEventStream`) rather than the native `EventSource` API, because
 * `EventSource` cannot send an `Authorization` header - and putting the
 * bearer token in the URL as a query string would leak it into server/proxy
 * logs (OWASP A02/A09).
 */
export function useWorkflowEventStream(sessionId: string | null): WorkflowEventStreamState {
  const [events, setEvents] = useState<WorkflowStreamEvent[]>([]);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    setEvents([]);
    setConnected(false);
    if (!sessionId) return;

    const controller = new AbortController();
    let cancelled = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;

    const processFrame = (frame: string) => {
      const dataLines = frame
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trim());
      if (dataLines.length === 0) return; // keepalive comment or blank frame
      try {
        const event = JSON.parse(dataLines.join("\n")) as WorkflowStreamEvent;
        setEvents((prev) => [...prev.slice(-(MAX_RECENT_EVENTS - 1)), event]);
      } catch {
        // Malformed frame - ignore rather than crash the whole stream.
      }
    };

    const connect = async () => {
      try {
        const response = await openEventStream(
          `/sessions/${sessionId}/workflow-events/stream`,
          controller.signal,
        );
        if (cancelled || !response.body) return;
        setConnected(true);

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (!cancelled) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          let separatorIndex = buffer.indexOf("\n\n");
          while (separatorIndex !== -1) {
            processFrame(buffer.slice(0, separatorIndex));
            buffer = buffer.slice(separatorIndex + 2);
            separatorIndex = buffer.indexOf("\n\n");
          }
        }
      } catch {
        // Aborted (unmount/session change) or a transient network error -
        // either way, fall through to the reconnect scheduling below.
      } finally {
        if (!cancelled) {
          setConnected(false);
          reconnectTimer = setTimeout(() => void connect(), RECONNECT_DELAY_MS);
        }
      }
    };

    void connect();

    return () => {
      cancelled = true;
      controller.abort();
      if (reconnectTimer) clearTimeout(reconnectTimer);
    };
  }, [sessionId]);

  return { events, lastEvent: events.length > 0 ? events[events.length - 1] : null, connected };
}
