import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders, mockFetchSequence } from "./testUtils";
import { buildApprovalRequests, buildWorkflowRunResult, FIXTURE_SESSION_ID, FIXTURE_WORKFLOW_RUN_ID } from "./fixtures";
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
      {
        match: "/approvals",
        response: buildApprovalRequests({
          id: "approval-security-assessment",
          checkpoint_id: "security-assessment-approval",
          requested_by_agent_id: "genie-orchestrator",
          subject_id: "security-assessment",
          status: "pending",
        }),
      },
      { match: "/decide", response: { id: "decision-1" } },
      { match: "/resume", response: { status: "running" } },
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

    await waitFor(() => {
      expect(fetchMock.mock.calls.some((call) => String(call[0]).endsWith("/decide"))).toBe(true);
    });
    await waitFor(() => {
      expect(fetchMock.mock.calls.some((call) => String(call[0]).endsWith("/resume"))).toBe(true);
    });
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
        match: "/approvals",
        response: buildApprovalRequests(),
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
        match: "/approvals",
        response: buildApprovalRequests(),
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

  it("queues the proceed action and shows 'Waiting for checkpoint...' if the user clicks Proceed before the approval checkpoint has loaded, then fires automatically once it arrives", async () => {
    // `mockFetchSequence` always returns the FIRST handler matching a given
    // URL suffix (it doesn't advance through a list of responses per
    // pattern), so a custom stub is used here to simulate the approvals
    // poll returning something different on its second call, and to keep
    // the workflow-events stream open so a later step_completed event can
    // be pushed after the button click - reproducing the real lag window
    // between the UI showing generated code and the backend's
    // security-assessment approval checkpoint actually being created.
    let approvalsCallCount = 0;
    const streamControllerRef: { current: ReadableStreamDefaultController<Uint8Array> | null } = {
      current: null,
    };
    const encoder = new TextEncoder();
    const calls: string[] = [];

    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      calls.push(url);
      const pathname = new URL(url).pathname;

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
        approvalsCallCount += 1;
        // First poll: the checkpoint hasn't been created server-side yet.
        // Second+ poll (triggered by the step_completed event below): it's
        // ready.
        const response =
          approvalsCallCount === 1
            ? buildApprovalRequests()
            : buildApprovalRequests({
                id: "approval-security-assessment",
                checkpoint_id: "security-assessment-approval",
                requested_by_agent_id: "genie-orchestrator",
                subject_id: "security-assessment",
                status: "pending",
              });
        return new Response(JSON.stringify(response), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (pathname.endsWith("/workflow-events/stream")) {
        const stream = new ReadableStream<Uint8Array>({
          start(controller) {
            streamControllerRef.current = controller;
          },
        });
        return new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } });
      }
      if (pathname.endsWith("/decide")) {
        return new Response(JSON.stringify({ id: "decision-1" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (pathname.endsWith("/resume")) {
        return new Response(JSON.stringify({ status: "running" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      throw new Error(`No mock handler registered for URL: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(<WorkshopPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    await waitFor(() => expect(screen.getByText(/Generated UI Code/i)).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(
      screen.getByRole("checkbox", {
        name: /AI can perform mistake, the user has reviewed and is willing to proceed/i,
      }),
    );
    await user.click(screen.getByRole("button", { name: /Proceed to Deploy & Launch/i }));

    // Approval checkpoint not ready yet - button reflects that instead of
    // silently doing nothing.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Waiting for checkpoint/i })).toBeInTheDocument(),
    );
    expect(calls.some((url) => url.endsWith("/decide"))).toBe(false);

    // The security-assessment step completes server-side - the live event
    // stream reports it, which refreshes the approvals poll and reveals the
    // now-pending checkpoint, firing the queued proceed action on its own.
    const stepCompletedEvent = {
      event_type: "step_completed",
      session_id: FIXTURE_SESSION_ID,
      workflow_run_id: FIXTURE_WORKFLOW_RUN_ID,
      step_id: "security-assessment",
      agent_id: "security-assessment-agent",
      delta: null,
      output_preview: "Security assessment complete.",
      error: null,
      emitted_at: "2026-07-23T10:02:00Z",
    };
    streamControllerRef.current?.enqueue(encoder.encode(`data: ${JSON.stringify(stepCompletedEvent)}\n\n`));

    await waitFor(() => {
      expect(calls.some((url) => url.endsWith("/decide"))).toBe(true);
    });
    await waitFor(() => {
      expect(calls.some((url) => url.endsWith("/resume"))).toBe(true);
    });

    streamControllerRef.current?.close();
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
        match: "/approvals",
        response: buildApprovalRequests(),
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

  it("forwards the governance policies selected on Architecture Studio to peer-review on proceed", async () => {
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
      {
        match: "/approvals",
        response: buildApprovalRequests({
          id: "approval-security-assessment",
          checkpoint_id: "security-assessment-approval",
          requested_by_agent_id: "genie-orchestrator",
          subject_id: "security-assessment",
          status: "pending",
        }),
      },
      { match: "/decide", response: { id: "decision-1" } },
      { match: "/resume", response: { status: "running" } },
    ]);

    renderWithProviders(<WorkshopPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
      governancePolicies: "Must use managed identity (no embedded credentials)",
    });

    await waitFor(() => expect(screen.getByText(/Generated UI Code/i)).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(
      screen.getByRole("checkbox", {
        name: /AI can perform mistake, the user has reviewed and is willing to proceed/i,
      }),
    );
    await user.click(screen.getByRole("button", { name: /Proceed to Deploy & Launch/i }));

    await waitFor(() => {
      const resumeCall = fetchMock.mock.calls.find((call) => String(call[0]).endsWith("/resume"));
      expect(resumeCall).toBeDefined();
      const [, resumeInit] = resumeCall as unknown as [string, RequestInit];
      const body = JSON.parse(resumeInit.body as string);
      expect(body.step_inputs["peer-review"].variables.policies).toBe(
        "Must use managed identity (no embedded credentials)",
      );
    });
  });
});
