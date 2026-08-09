import { describe, expect, it, vi } from "vitest";
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
      { match: "/approvals", response: [] },
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

    // Proceeding checks for (and would decide) a pending build-review-approval
    // request - the human review this checkbox/click already represents - so
    // Deploy & Launch never needs a separate/duplicate approval action. No
    // pending request exists in this fixture, so nothing is decided, and no
    // workflow steps are resumed from here.
    await waitFor(() =>
      expect(fetchMock.mock.calls.some((call) => String(call[0]).endsWith("/approvals"))).toBe(true),
    );
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

  it("surfaces missionError (set when Architecture Studio's fire-and-forget resume call fails) with a retry that re-kicks off build-solution", async () => {
    const fetchMock = mockFetchSequence([
      {
        match: `/workflows/runs/${FIXTURE_WORKFLOW_RUN_ID}`,
        response: buildWorkflowRunResult({ step_results: [] }),
      },
    ]);

    renderWithProviders(<WorkshopPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
      missionError: { message: "Failed to start UI & Agent Design after the architecture approval." },
    });

    await waitFor(() =>
      expect(
        screen.getByText(/Failed to start UI & Agent Design after the architecture approval/i),
      ).toBeInTheDocument(),
    );

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => {
      const resumeCall = fetchMock.mock.calls.find((call) => String(call[0]).endsWith("/resume"));
      expect(resumeCall).toBeDefined();
      const [, resumeInit] = resumeCall as unknown as [string, RequestInit];
      const body = JSON.parse(resumeInit.body as string);
      expect(body.step_inputs["build-solution"]).toBeDefined();
    });
  });

  it(
    "treats build-solution as stuck (never actually started server-side) after a grace period with no result and no live stream, offering a retry",
    async () => {
      // Fake only the setTimeout/clearTimeout pair the 45s stuck-detector
      // uses - leave setInterval/queueMicrotask/Date alone so the
      // unrelated live-stream reconnect loop (useWorkflowEventStream,
      // itself driven by real Promise microtasks) keeps resolving
      // normally instead of needing to be ticked forward here too.
      vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
      try {
        const fetchMock = mockFetchSequence([
          {
            match: `/workflows/runs/${FIXTURE_WORKFLOW_RUN_ID}`,
            response: buildWorkflowRunResult({ step_results: [] }),
          },
        ]);

        renderWithProviders(<WorkshopPage />, {
          sessionId: FIXTURE_SESSION_ID,
          workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
        });

        expect(screen.queryByText(/hasn't started yet/i)).not.toBeInTheDocument();

        await vi.advanceTimersByTimeAsync(45_000);

        expect(screen.getByText(/UI & Agent Design hasn't started yet/i)).toBeInTheDocument();

        vi.useRealTimers();
        const user = userEvent.setup();
        await user.click(screen.getByRole("button", { name: "Retry" }));

        await waitFor(() => {
          const resumeCall = fetchMock.mock.calls.find((call) =>
            String(call[0]).endsWith("/resume"),
          );
          expect(resumeCall).toBeDefined();
        });
      } finally {
        vi.useRealTimers();
      }
    },
    15_000,
  );
});
