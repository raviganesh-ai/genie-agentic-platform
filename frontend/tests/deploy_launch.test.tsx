import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders, mockFetchSequence } from "./testUtils";
import { FIXTURE_SESSION_ID, FIXTURE_WORKFLOW_RUN_ID } from "./fixtures";
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
});
