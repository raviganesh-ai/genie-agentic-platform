import type { ReactElement } from "react";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { FluentProvider } from "@fluentui/react-components";
import { genieDarkTheme } from "@/styles/theme";
import { SessionProvider } from "@/state/SessionContext";
import type { SafeError } from "@/types/common";

/**
 * Renders a page/component wrapped in the same providers the real app uses
 * (FluentProvider + SessionProvider + Router), with optional preset
 * sessionId/workflowRunId so page tests don't need to click through
 * LandingPage/MissionControlPage first.
 */
export function renderWithProviders(
  ui: ReactElement,
  options: {
    sessionId?: string | null;
    workflowRunId?: string | null;
    missionStartedAt?: number | null;
    missionError?: SafeError | null;
    governancePolicies?: string;
  } = {},
) {
  return render(
    <FluentProvider theme={genieDarkTheme}>
      <MemoryRouter>
        <SessionProvider
          initialSessionId={options.sessionId ?? null}
          initialWorkflowRunId={options.workflowRunId ?? null}
          initialMissionStartedAt={options.missionStartedAt ?? null}
          initialMissionError={options.missionError ?? null}
          initialGovernancePolicies={options.governancePolicies ?? ""}
        >
          {ui}
        </SessionProvider>
      </MemoryRouter>
    </FluentProvider>,
  );
}

/** Installs a `global.fetch` stub that resolves based on a pathname suffix match. */
export function mockFetchSequence(
  handlers: Array<{
    match: string;
    response?: unknown;
    status?: number;
    /**
     * Raw SSE frames (each including its own trailing `\n\n`) to stream back
     * for this handler instead of a JSON body - use this to simulate the
     * `/workflow-events/stream` route emitting real `step_started`/
     * `step_delta`/... events for a test, instead of the default
     * immediately-closed empty stream below.
     */
    sseChunks?: string[];
  }>,
) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const pathname = new URL(url).pathname;
    const handler = handlers.find((h) => pathname.endsWith(h.match));
    if (handler?.sseChunks) {
      const encoder = new TextEncoder();
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          for (const chunk of handler.sseChunks ?? []) controller.enqueue(encoder.encode(chunk));
          controller.close();
        },
      });
      return new Response(stream, {
        status: handler.status ?? 200,
        headers: { "Content-Type": "text/event-stream" },
      });
    }
    // Every page now opens a live workflow-events SSE connection
    // (useWorkflowEventStream) alongside its normal polled fetches. Tests
    // that don't care about that stream shouldn't need to register a
    // handler for it explicitly - resolve it with an immediately-closed
    // empty stream so the hook connects, sees no events, and stops.
    if (pathname.endsWith("/workflow-events/stream")) {
      return new Response(new ReadableStream({ start: (controller) => controller.close() }), {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      });
    }
    if (!handler) {
      throw new Error(`No mock handler registered for URL: ${url}`);
    }
    return new Response(JSON.stringify(handler.response), {
      status: handler.status ?? 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

