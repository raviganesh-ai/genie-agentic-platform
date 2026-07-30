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
  /**
   * Every `step_delta` chunk received so far, concatenated in arrival order
   * and keyed by `${step_id}::${agent_id}` - deliberately NOT subject to the
   * `MAX_RECENT_EVENTS` cap on `events` (that cap is fine for "trigger a
   * refresh on any event" consumers, but would silently drop real generated
   * content for a long-running step, e.g. the Build Agent's multi-minute
   * code generation). Reset to an empty string for a key when a fresh
   * `step_started` arrives for it (a re-run of that step), so stale content
   * from a prior run is never shown alongside a new one.
   */
  stepDeltaText: Record<string, string>;
}

/** Builds the `stepDeltaText` lookup key for a given step/agent pair. */
export function workflowStepDeltaKey(stepId: string, agentId: string): string {
  return `${stepId}::${agentId}`;
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
  const [stepDeltaText, setStepDeltaText] = useState<Record<string, string>>({});

  useEffect(() => {
    setEvents([]);
    setConnected(false);
    setStepDeltaText({});
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

        const key = workflowStepDeltaKey(event.step_id, event.agent_id);
        if (event.event_type === "step_started") {
          setStepDeltaText((prev) => ({ ...prev, [key]: "" }));
        } else if (event.event_type === "step_delta" && event.delta) {
          setStepDeltaText((prev) => ({ ...prev, [key]: (prev[key] ?? "") + event.delta }));
        }
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

  return {
    events,
    lastEvent: events.length > 0 ? events[events.length - 1] : null,
    connected,
    stepDeltaText,
  };
}
