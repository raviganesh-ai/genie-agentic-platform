import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { renderWithProviders, mockFetchSequence } from "./testUtils";
import { buildWorkflowRunResult, FIXTURE_SESSION_ID, FIXTURE_WORKFLOW_RUN_ID } from "./fixtures";
import { WorkshopPage } from "@/features/workshop-center/WorkshopPage";

describe("WorkshopPage", () => {
  it("immediately names the component being generated before its first code delta arrives", async () => {
    const componentStarted = {
      event_type: "component_started",
      session_id: FIXTURE_SESSION_ID,
      workflow_run_id: FIXTURE_WORKFLOW_RUN_ID,
      step_id: "build-solution",
      agent_id: "build-agent",
      component_name: "Ticket Classifier Agent",
      delta: null,
      output_preview: null,
      error: null,
      emitted_at: "2026-07-23T10:00:00Z",
    };

    mockFetchSequence([
      {
        match: `/workflows/runs/${FIXTURE_WORKFLOW_RUN_ID}`,
        response: buildWorkflowRunResult({ status: "running", step_results: [] }),
      },
      {
        match: "/workflow-events/stream",
        sseChunks: [`data: ${JSON.stringify(componentStarted)}\n\n`],
      },
    ]);

    renderWithProviders(<WorkshopPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    await waitFor(() =>
      expect(screen.getByText("Generating Ticket Classifier Agent...")).toBeInTheDocument(),
    );
  });

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
      {
        match: `/workshop/build-components/${FIXTURE_WORKFLOW_RUN_ID}/validate`,
        response: { valid: true },
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

    const callsBeforeProceed = fetchMock.mock.calls.length;
    await user.click(screen.getByRole("button", { name: /Proceed to Deploy & Launch/i }));

    await waitFor(() =>
      expect(fetchMock.mock.calls.length).toBeGreaterThan(callsBeforeProceed),
    );
    expect(
      fetchMock.mock.calls.some((call) =>
        String(call[0]).endsWith(
          `/workshop/build-components/${FIXTURE_WORKFLOW_RUN_ID}/validate`,
        ),
      ),
    ).toBe(true);
    expect(fetchMock.mock.calls.some((call) => String(call[0]).endsWith("/decide"))).toBe(false);
    expect(fetchMock.mock.calls.some((call) => String(call[0]).endsWith("/resume"))).toBe(false);
  });

  it("keeps an invalid reviewed build out of Deploy & Launch when the automatic repair attempt also fails", async () => {
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
      { match: "/approvals", response: [] },
      {
        match: `/workshop/build-components/${FIXTURE_WORKFLOW_RUN_ID}/validate`,
        status: 422,
        response: {
          detail: "The generated orchestrator does not execute every specialist.",
          component: "orchestrator",
        },
      },
      {
        match: "/resume",
        response: buildWorkflowRunResult({ status: "completed", step_results: [] }),
      },
    ]);

    renderWithProviders(<WorkshopPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("checkbox", { name: /AI can perform mistake/i }),
    );
    await user.click(screen.getByRole("button", { name: /Proceed to Deploy & Launch/i }));

    // The same failure persists on the mocked backend even after the
    // automatic repair attempt (both validate calls hit the same 422
    // handler above) - the banner must still show once that one automatic
    // attempt is exhausted, and a manual retry must remain available.
    const validationError = await screen.findByText(
      /Automatic repair did not resolve this validation failure.*does not execute every specialist/,
    );
    expect(validationError).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Try automatic repair again" }),
    ).toBeInTheDocument();
  });

  it("automatically repairs and re-validates the exact failing component (with its exact validation error as guidance) as soon as Proceed to Deploy & Launch fails, navigating through without requiring a second click", async () => {
    let validateCallCount = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      const pathname = new URL(url).pathname;
      if (pathname.endsWith("/workflow-events/stream")) {
        return new Response(new ReadableStream({ start: (controller) => controller.close() }), {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        });
      }
      if (pathname.endsWith(`/workflows/runs/${FIXTURE_WORKFLOW_RUN_ID}`)) {
        return new Response(
          JSON.stringify(
            buildWorkflowRunResult({
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
          ),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (pathname.endsWith("/approvals")) {
        return new Response(JSON.stringify([]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (pathname.endsWith(`/workshop/build-components/${FIXTURE_WORKFLOW_RUN_ID}/validate`)) {
        validateCallCount += 1;
        if (validateCallCount === 1) {
          return new Response(
            JSON.stringify({
              detail:
                "The generated mission UI's submit payload groups fields inside nested objects.",
              component: "ui",
            }),
            { status: 422, headers: { "Content-Type": "application/json" } },
          );
        }
        return new Response(JSON.stringify({}), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (pathname.endsWith("/resume")) {
        return new Response(
          JSON.stringify(buildWorkflowRunResult({ status: "completed", step_results: [] })),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      throw new Error(`No mock handler registered for URL: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(
      <Routes>
        <Route path="/" element={<WorkshopPage />} />
        <Route path="/outputs" element={<div>Outputs reached</div>} />
      </Routes>,
      {
        sessionId: FIXTURE_SESSION_ID,
        workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
        governancePolicies: "Never expose PII.",
      },
    );

    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("checkbox", { name: /AI can perform mistake/i }),
    );
    await user.click(screen.getByRole("button", { name: /Proceed to Deploy & Launch/i }));

    await waitFor(() => {
      const resumeCall = fetchMock.mock.calls.find((call) => String(call[0]).endsWith("/resume"));
      expect(resumeCall).toBeDefined();
      const [, resumeInit] = resumeCall as unknown as [string, RequestInit];
      const body = JSON.parse(resumeInit.body as string);
      const stepInput = body.step_inputs["build-solution"];
      expect(stepInput.variables.regenerate_components).toBe("ui");
      expect(stepInput.variables.policies).toContain("Never expose PII.");
      expect(stepInput.variables.policies).toContain(
        "The generated mission UI's submit payload groups fields inside nested objects.",
      );
    });

    // No error banner ever appears, and no second click is required - the
    // one automatic repair attempt succeeded (the second mocked validate
    // call returns 200) and the page moved straight on to Deploy & Launch.
    await screen.findByText("Outputs reached");
    expect(screen.queryByText(/Automatic repair did not resolve/)).not.toBeInTheDocument();
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
    expect(screen.getByLabelText("Streaming code")).toHaveTextContent("export function App");
    expect(
      screen.queryByRole("checkbox", { name: /AI can perform mistake/i }),
    ).not.toBeInTheDocument();
  });

  it("renders accumulated component code from polling when the SSE stream was missed", async () => {
    mockFetchSequence([
      {
        match: `/workflows/runs/${FIXTURE_WORKFLOW_RUN_ID}`,
        response: buildWorkflowRunResult({ status: "running", step_results: [] }),
      },
      {
        match: `/workshop/build-components/${FIXTURE_WORKFLOW_RUN_ID}`,
        response: {
          build_output:
            "```python\n# agent: Ticket Classifier Agent\nclass TicketClassifierAgent:\n    pass\n```",
        },
      },
    ]);

    renderWithProviders(<WorkshopPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    await waitFor(() =>
      expect(screen.getByText(/Generated Agent Code - Ticket Classifier Agent/i)).toBeInTheDocument(),
    );
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
      // Fake only the setTimeout/clearTimeout pair the 5-minute
      // stuck-detector uses - leave setInterval/queueMicrotask/Date alone
      // so the unrelated live-stream reconnect loop
      // (useWorkflowEventStream, itself driven by real Promise microtasks)
      // keeps resolving normally instead of needing to be ticked forward
      // here too.
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

        await vi.advanceTimersByTimeAsync(300_000);

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

  it(
    "does not treat build-solution as stuck once a step_started live event has arrived, even with no delta text yet",
    async () => {
      // A `step_started` event alone (no content chunk yet) is already
      // direct proof the step began server-side - e.g. right after
      // switching tabs away and back, a fresh SSE connection may not see
      // another delta for a while even though generation is genuinely
      // still in progress. This must not show the "hasn't started" banner.
      vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
      try {
        const startedEvent = {
          event_type: "step_started",
          session_id: FIXTURE_SESSION_ID,
          workflow_run_id: FIXTURE_WORKFLOW_RUN_ID,
          step_id: "build-solution",
          agent_id: "build-agent",
          delta: null,
          output_preview: null,
          error: null,
          emitted_at: "2026-07-23T10:00:00Z",
        };

        mockFetchSequence([
          {
            match: `/workflows/runs/${FIXTURE_WORKFLOW_RUN_ID}`,
            response: buildWorkflowRunResult({ step_results: [] }),
          },
          {
            match: "/workflow-events/stream",
            sseChunks: [`data: ${JSON.stringify(startedEvent)}\n\n`],
          },
        ]);

        renderWithProviders(<WorkshopPage />, {
          sessionId: FIXTURE_SESSION_ID,
          workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
        });

        await vi.waitFor(() =>
          expect(
            screen.getByText(/Genie is calling the Orchestrator Agent/i),
          ).toBeInTheDocument(),
        );

        // Wait for the step_started event to actually be reflected in
        // rendered state (not just for the banner's default text, which
        // shows identically before the event has been processed too) -
        // otherwise advancing the timer below can race the mock SSE
        // stream's own microtask-driven delivery and flake under CI's
        // heavier scheduling contention.
        await vi.waitFor(() =>
          expect(screen.getByText(/build-agent \(build solution\) started/i)).toBeInTheDocument(),
        );

        await vi.advanceTimersByTimeAsync(300_000);

        expect(screen.queryByText(/hasn't started yet/i)).not.toBeInTheDocument();
      } finally {
        vi.useRealTimers();
      }
    },
    15_000,
  );
});
