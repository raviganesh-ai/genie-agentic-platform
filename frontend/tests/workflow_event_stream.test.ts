import { describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { useWorkflowEventStream } from "@/hooks/useWorkflowEventStream";
import type { WorkflowStreamEvent } from "@/types/workflowEvents";

function sseResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

const SAMPLE_EVENT: WorkflowStreamEvent = {
  event_type: "step_completed",
  session_id: "session-1",
  workflow_run_id: "run-1",
  step_id: "step-a",
  agent_id: "requirements-analyst",
  delta: null,
  output_preview: "done",
  error: null,
  emitted_at: "2026-07-28T00:00:00Z",
};

describe("useWorkflowEventStream", () => {
  it("parses SSE data frames into events and exposes the most recent one", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => sseResponse([`data: ${JSON.stringify(SAMPLE_EVENT)}\n\n`])),
    );

    const { result } = renderHook(() => useWorkflowEventStream("session-1"));

    await waitFor(() => expect(result.current.events).toHaveLength(1));
    expect(result.current.lastEvent).toEqual(SAMPLE_EVENT);
  });

  it("ignores keepalive comment frames", async () => {
    const fetchMock = vi.fn(async () => sseResponse([": keepalive\n\n"]));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useWorkflowEventStream("session-1"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    // Give the stream a tick to fully drain before asserting nothing was recorded.
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(result.current.events).toHaveLength(0);
  });

  it("does not open a connection when there is no active session", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useWorkflowEventStream(null));

    expect(result.current.connected).toBe(false);
    expect(result.current.events).toHaveLength(0);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
