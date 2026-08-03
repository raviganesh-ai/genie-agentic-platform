import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders, mockFetchSequence } from "./testUtils";
import { FIXTURE_SESSION_ID, FIXTURE_WORKFLOW_RUN_ID, buildApprovalRequests } from "./fixtures";
import { DeployLaunchPage } from "@/features/deploy-launch/DeployLaunchPage";
import type { DeploymentPipelineRun } from "@/types/deployLaunch";

function buildPipelineRun(overrides: Partial<DeploymentPipelineRun> = {}): DeploymentPipelineRun {
  return {
    id: "pipeline-run-1",
    session_id: FIXTURE_SESSION_ID,
    workflow_run_id: FIXTURE_WORKFLOW_RUN_ID,
    status: "pending",
    steps: [],
    access_policy: null,
    backend_url: null,
    frontend_url: null,
    launch_url: null,
    test_summary: null,
    security_findings_count: null,
    created_at: "2026-07-23T12:00:00Z",
    updated_at: "2026-07-23T12:00:00Z",
    ...overrides,
  };
}

describe("DeployLaunchPage", () => {
  it("starts the pipeline for the active workflow run", async () => {
    const fetchMock = mockFetchSequence([
      { match: "/deploy-launch/", response: [] },
      { match: "/deploy-launch/start", response: buildPipelineRun({ status: "running" }) },
    ]);

    renderWithProviders(<DeployLaunchPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    const user = userEvent.setup();
    const startButton = await screen.findByRole("button", { name: /Start Deploy & Launch/i });
    await user.click(startButton);

    await waitFor(() => {
      const startCall = fetchMock.mock.calls.find((call) => String(call[0]).endsWith("/deploy-launch/start"));
      expect(startCall).toBeDefined();
      const [, startInit] = startCall as unknown as [string, RequestInit];
      const body = JSON.parse(startInit.body as string);
      expect(body.workflow_run_id).toBe(FIXTURE_WORKFLOW_RUN_ID);
    });
  });

  it("shows step progress for an in-flight pipeline run", async () => {
    mockFetchSequence([
      {
        match: "/deploy-launch/",
        response: [
          buildPipelineRun({
            status: "running",
            steps: [
              {
                step_id: "generate-access-policy",
                name: "Generate Access Policy & Least Access",
                status: "completed",
                detail: "Generated.",
                error: null,
                started_at: "2026-07-23T12:00:00Z",
                completed_at: "2026-07-23T12:00:01Z",
              },
            ],
          }),
        ],
      },
    ]);

    renderWithProviders(<DeployLaunchPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    await waitFor(() => expect(screen.getByText(/Generate Access Policy & Least Access/i)).toBeInTheDocument());
    expect(screen.getByText(/^Launch$/i)).toBeInTheDocument();
  });

  it("shows the launch link and download action once the pipeline completes", async () => {
    mockFetchSequence([
      {
        match: "/deploy-launch/",
        response: [
          buildPipelineRun({
            status: "completed",
            launch_url: "https://genie-i4opvs55x5qu4-swa.azurestaticapps.net/",
            steps: [
              {
                step_id: "generate-access-policy",
                name: "Generate Access Policy & Least Access",
                status: "completed",
                detail: "Generated.",
                error: null,
                started_at: "2026-07-23T12:00:00Z",
                completed_at: "2026-07-23T12:00:01Z",
              },
            ],
          }),
        ],
      },
    ]);

    renderWithProviders(<DeployLaunchPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    await waitFor(() =>
      expect(
        screen.getByRole("link", { name: /genie-i4opvs55x5qu4-swa\.azurestaticapps\.net/i }),
      ).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: /Download Code & Access Policy/i })).toBeInTheDocument();
  });

  it("automatically decides a pending Final Output Approval and retries, with no separate approval screen", async () => {
    // The user already gave their one risk acknowledgment on Workshop
    // (see workshop_center.test.tsx) - Deploy & Launch must not surface a
    // second manual approval action for the same checkpoint.
    let startCallCount = 0;
    const calls: string[] = [];

    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      calls.push(url);
      const pathname = new URL(url).pathname;

      if (pathname.endsWith("/deploy-launch/") && !pathname.includes("/start")) {
        return new Response(JSON.stringify([]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (pathname.endsWith("/deploy-launch/start")) {
        startCallCount += 1;
        if (startCallCount === 1) {
          return new Response(JSON.stringify({ message: "Final Output Approval is pending." }), {
            status: 409,
            headers: { "Content-Type": "application/json" },
          });
        }
        const run: DeploymentPipelineRun = buildPipelineRun({ status: "running" });
        return new Response(JSON.stringify(run), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (pathname.endsWith("/approvals")) {
        return new Response(
          JSON.stringify(
            buildApprovalRequests({
              id: "approval-final-output",
              checkpoint_id: "final-output-approval",
              subject_id: FIXTURE_WORKFLOW_RUN_ID,
              status: "pending",
            }),
          ),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (pathname.endsWith("/decide")) {
        return new Response(JSON.stringify({ id: "decision-1" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (pathname.endsWith("/workflow-events/stream")) {
        return new Response(new ReadableStream<Uint8Array>({ start: () => undefined }), {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        });
      }
      throw new Error(`Unhandled fetch in test: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(<DeployLaunchPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    const user = userEvent.setup();
    const startButton = await screen.findByRole("button", { name: /Start Deploy & Launch/i });
    await user.click(startButton);

    await waitFor(() => {
      expect(calls.some((url) => url.endsWith("/decide"))).toBe(true);
    });
    await waitFor(() => {
      expect(startCallCount).toBe(2);
    });
    expect(screen.queryByRole("button", { name: /Approve Final Output/i })).not.toBeInTheDocument();

    vi.unstubAllGlobals();
  });
});
