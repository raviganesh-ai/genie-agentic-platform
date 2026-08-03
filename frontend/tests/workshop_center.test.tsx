import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders, mockFetchSequence } from "./testUtils";
import { buildWorkflowRunResult, FIXTURE_SESSION_ID, FIXTURE_WORKFLOW_RUN_ID } from "./fixtures";
import { WorkshopPage } from "@/features/workshop-center/WorkshopPage";

describe("WorkshopPage", () => {
  it("shows only the generated code, and reveals Proceed to Deploy & Launch once the review checkbox is checked", async () => {
    const fetchMock = mockFetchSequence([
      {
        match: `/workflows/runs/${FIXTURE_WORKFLOW_RUN_ID}`,
        response: buildWorkflowRunResult({
          step_results: [
            {
              step_id: "build-solution",
              agent_id: "orchestrator",
              status: "completed",
              output_text: "```tsx\n// agent: ui\nexport function App() { return null; }\n```",
              error: null,
              started_at: "2026-07-23T10:00:00Z",
              completed_at: "2026-07-23T10:01:00Z",
            },
          ],
        }),
      },
    ]);

    renderWithProviders(<WorkshopPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    await waitFor(() =>
      expect(screen.getByText(/Generated UI Code/i)).toBeInTheDocument(),
    );

    // No chat/priority controls left on the page - just the generated code.
    expect(screen.queryByPlaceholderText(/Ask a question or provide direction/i)).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/Describe the priority change/i)).not.toBeInTheDocument();

    expect(
      screen.queryByRole("button", { name: /Proceed to Deploy & Launch/i }),
    ).not.toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(
      screen.getByRole("checkbox", {
        name: /AI can perform mistake, the user has reviewed and is willing to proceed/i,
      }),
    );

    expect(screen.getByRole("button", { name: /Proceed to Deploy & Launch/i })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Proceed to Deploy & Launch/i }));

    // Proceeding goes straight to Deploy & Launch - no approval checkpoint
    // is decided and no workflow steps are resumed in the background.
    expect(fetchMock.mock.calls.some((call) => String(call[0]).endsWith("/decide"))).toBe(false);
    expect(fetchMock.mock.calls.some((call) => String(call[0]).endsWith("/resume"))).toBe(false);
  });

  it("shows the review checkbox once the Build Agent's live streamed UI code block closes, even before build-solution is marked completed server-side", async () => {
    const deltaEvent = (delta: string) => ({
      event_type: "step_delta",
      session_id: FIXTURE_SESSION_ID,
      workflow_run_id: FIXTURE_WORKFLOW_RUN_ID,
      step_id: "build-solution",
      agent_id: "build-agent",
      delta,
      output_preview: null,
      error: null,
      emitted_at: "2026-07-23T10:00:00Z",
    });

    mockFetchSequence([
      {
        // build-solution hasn't completed yet - no step result for it at all,
        // which is exactly the ~15-minute "nothing shows" window this
        // streaming feature targets.
        match: `/workflows/runs/${FIXTURE_WORKFLOW_RUN_ID}`,
        response: buildWorkflowRunResult({ step_results: [] }),
      },
      {
        match: "/workflow-events/stream",
        sseChunks: [
          `data: ${JSON.stringify(deltaEvent("```tsx\n// agent: ui\nexport function App"))}\n\n`,
          `data: ${JSON.stringify(deltaEvent('() { return null; }\n```'))}\n\n`,
        ],
      },
    ]);

    renderWithProviders(<WorkshopPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    await waitFor(() => expect(screen.getByText(/Generated UI Code/i)).toBeInTheDocument());
    // The UI code block (always generated last) has already closed in the
    // live stream above, so the review checkbox should appear right away -
    // it must not wait for genie-orchestrator's separate, slower verbatim
    // echo to finish and populate build-solution's own step result.
    expect(
      screen.getByRole("checkbox", { name: /AI can perform mistake/i }),
    ).toBeInTheDocument();
  });

  it("does not show the review checkbox while the Build Agent is still streaming and no component has closed yet", async () => {
    const deltaEvent = (delta: string) => ({
      event_type: "step_delta",
      session_id: FIXTURE_SESSION_ID,
      workflow_run_id: FIXTURE_WORKFLOW_RUN_ID,
      step_id: "build-solution",
      agent_id: "build-agent",
      delta,
      output_preview: null,
      error: null,
      emitted_at: "2026-07-23T10:00:00Z",
    });

    mockFetchSequence([
      {
        match: `/workflows/runs/${FIXTURE_WORKFLOW_RUN_ID}`,
        response: buildWorkflowRunResult({ step_results: [] }),
      },
      {
        match: "/workflow-events/stream",
        // Still an open, unclosed fence - the component hasn't finished
        // streaming yet, so no checkbox should be offered.
        sseChunks: [`data: ${JSON.stringify(deltaEvent("```tsx\n// agent: ui\nexport function App"))}\n\n`],
      },
    ]);

    renderWithProviders(<WorkshopPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    await waitFor(() => expect(screen.getByText(/Generating UI Code/i)).toBeInTheDocument());
    expect(
      screen.queryByRole("checkbox", { name: /AI can perform mistake/i }),
    ).not.toBeInTheDocument();
  });

  it("regenerates only the UI component's code after the user types an instruction and clicks Regenerate", async () => {
    mockFetchSequence([
      {
        match: `/workflows/runs/${FIXTURE_WORKFLOW_RUN_ID}`,
        response: buildWorkflowRunResult({
          step_results: [
            {
              step_id: "build-solution",
              agent_id: "orchestrator",
              status: "completed",
              output_text: "```tsx\n// agent: ui\nexport function App() { return null; }\n```",
              error: null,
              started_at: "2026-07-23T10:00:00Z",
              completed_at: "2026-07-23T10:01:00Z",
            },
          ],
        }),
      },
      {
        match: "/regenerate-component",
        response: {
          component_label: "ui",
          code: "```tsx\n// agent: ui\nexport function App() { return 'updated'; }\n```",
        },
      },
    ]);

    renderWithProviders(<WorkshopPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    await waitFor(() => expect(screen.getByText(/Generated UI Code/i)).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Regenerate" }));
    await user.type(
      screen.getByPlaceholderText(/Make the header sticky/i),
      "Make the header sticky.",
    );
    await user.click(screen.getByRole("button", { name: "Regenerate" }));

    await waitFor(() => expect(screen.getByText(/return 'updated'/)).toBeInTheDocument());
    expect(screen.queryByText(/return null/)).not.toBeInTheDocument();
  });
});
