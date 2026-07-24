import type { ReactElement } from "react";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { FluentProvider } from "@fluentui/react-components";
import { genieDarkTheme } from "@/styles/theme";
import { SessionProvider } from "@/state/SessionContext";

/**
 * Renders a page/component wrapped in the same providers the real app uses
 * (FluentProvider + SessionProvider + Router), with optional preset
 * sessionId/workflowRunId so page tests don't need to click through
 * LandingPage/MissionControlPage first.
 */
export function renderWithProviders(
  ui: ReactElement,
  options: { sessionId?: string | null; workflowRunId?: string | null } = {},
) {
  return render(
    <FluentProvider theme={genieDarkTheme}>
      <MemoryRouter>
        <SessionProvider
          initialSessionId={options.sessionId ?? null}
          initialWorkflowRunId={options.workflowRunId ?? null}
        >
          {ui}
        </SessionProvider>
      </MemoryRouter>
    </FluentProvider>,
  );
}

/** Installs a `global.fetch` stub that resolves based on a pathname suffix match. */
export function mockFetchSequence(
  handlers: Array<{ match: string; response: unknown; status?: number }>,
) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    const pathname = new URL(url).pathname;
    const handler = handlers.find((h) => pathname.endsWith(h.match));
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
