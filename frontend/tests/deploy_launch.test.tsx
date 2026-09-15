import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders, mockFetchSequence } from "./testUtils";
import { FIXTURE_SESSION_ID, FIXTURE_WORKFLOW_RUN_ID } from "./fixtures";
import { DeployLaunchPage } from "@/features/deploy-launch/DeployLaunchPage";
import { DEPLOYMENT_STEP_NAMES, DEPLOYMENT_STEP_ORDER } from "@/types/deployLaunch";
import type { DeploymentPipelineRun } from "@/types/deployLaunch";

function buildPipelineRun(overrides: Partial<DeploymentPipelineRun> = {}): DeploymentPipelineRun {
  return {
    id: "pipeline-run-1",
    session_id: FIXTURE_SESSION_ID,
    workflow_run_id: FIXTURE_WORKFLOW_RUN_ID,
    status: "pending",
    steps: [],
    access_policy: null,
    provisioned_agents: [],
    backend_url: null,
    frontend_url: null,
    launch_url: null,
    security_scan_report: null,
    cost_report: null,
    created_at: "2026-07-23T12:00:00Z",
    updated_at: "2026-07-23T12:00:00Z",
    ...overrides,
  };
}

describe("DeployLaunchPage", () => {
  it("starts the pipeline automatically as soon as the page loads with no run yet - no manual click needed", async () => {
    const fetchMock = mockFetchSequence([
      { match: "/deploy-launch/", response: [] },
      { match: "/deploy-launch/start", response: buildPipelineRun({ status: "running" }) },
    ]);

    renderWithProviders(<DeployLaunchPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    await waitFor(() => {
      const startCall = fetchMock.mock.calls.find((call) => String(call[0]).endsWith("/deploy-launch/start"));
      expect(startCall).toBeDefined();
      const [, startInit] = startCall as unknown as [string, RequestInit];
      const body = JSON.parse(startInit.body as string);
      expect(body.workflow_run_id).toBe(FIXTURE_WORKFLOW_RUN_ID);
    });
  });

  it("shows the full step roster up front, all Not Started, before any run exists yet", async () => {
    mockFetchSequence([
      { match: "/deploy-launch/", response: [] },
      { match: "/deploy-launch/start", response: buildPipelineRun({ status: "running" }) },
    ]);

    renderWithProviders(<DeployLaunchPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    // The whole plan is visible immediately - no generic "Starting..."
    // spinner - every step name shows up front with a "Not Started" status.
    await waitFor(() => {
      for (const stepId of DEPLOYMENT_STEP_ORDER) {
        expect(screen.getByText(DEPLOYMENT_STEP_NAMES[stepId])).toBeInTheDocument();
      }
    });
    expect(screen.getAllByText("Not Started").length).toBeGreaterThan(0);
    expect(screen.queryByText("Starting Deploy & Launch automatically...")).not.toBeInTheDocument();
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

  it("shows an informational Security Copilot scan summary that never blocks Launch", async () => {
    mockFetchSequence([
      {
        match: "/deploy-launch/",
        response: [
          buildPipelineRun({
            status: "completed",
            launch_url: "https://genie-i4opvs55x5qu4-swa.azurestaticapps.net/",
            security_scan_report: {
              available: true,
              summary: "2 findings reviewed - none blocking.",
              findings: [
                {
                  severity: "medium",
                  title: "Missing security header",
                  description: "Content-Security-Policy header not set.",
                  resource: "frontend",
                },
              ],
              reference_url: "https://portal.azure.com/securitycopilot/scan/1",
              scanned_at: "2026-07-23T12:05:00Z",
            },
          }),
        ],
      },
    ]);

    renderWithProviders(<DeployLaunchPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    expect(await screen.findByText("🛡️ Microsoft Security Copilot Scan")).toBeInTheDocument();
    expect(await screen.findByText("2 findings reviewed - none blocking.")).toBeInTheDocument();
    expect(screen.getByText("1 finding(s)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Launch$/i })).toBeInTheDocument();
  });

  it("shows an informational FinOps cost report summary that never blocks Launch", async () => {
    mockFetchSequence([
      {
        match: "/deploy-launch/",
        response: [
          buildPipelineRun({
            status: "completed",
            launch_url: "https://genie-i4opvs55x5qu4-swa.azurestaticapps.net/",
            cost_report: {
              available: true,
              summary: "Estimated spend for this mission so far.",
              total_cost: 12.5,
              currency: "USD",
              line_items: [{ resource_type: "Container App", cost: 12.5 }],
              period_start: "2026-07-01T00:00:00Z",
              period_end: "2026-07-23T00:00:00Z",
              reported_at: "2026-07-23T12:05:00Z",
            },
          }),
        ],
      },
    ]);

    renderWithProviders(<DeployLaunchPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    expect(await screen.findByText("💰 Azure FinOps Cost Report")).toBeInTheDocument();
    expect(await screen.findByText("Estimated spend for this mission so far.")).toBeInTheDocument();
    expect(screen.getByText("12.5 USD")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Launch$/i })).toBeInTheDocument();
  });

  it("shows a gamified 'Genie is working with...' activity banner for the currently running step", async () => {
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
                status: "running",
                detail: "",
                error: null,
                started_at: "2026-07-23T12:00:00Z",
                completed_at: null,
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
        screen.getByText(/Genie is working with the Orchestrator to generate your least-access policy/i),
      ).toBeInTheDocument(),
    );
  });

  it("shows a per-agent breakdown with its Foundry agent name for the Deploy Agents step", async () => {
    mockFetchSequence([
      {
        match: "/deploy-launch/",
        response: [
          buildPipelineRun({
            status: "running",
            steps: [
              {
                step_id: "provision-foundry-agents",
                name: "Deploy Agents to Foundry",
                status: "running",
                detail: "",
                error: null,
                started_at: "2026-07-23T12:00:00Z",
                completed_at: null,
              },
            ],
            provisioned_agents: [
              {
                agent_name: "Requirements Specialist",
                status: "completed",
                foundry_agent_name: "local-acme-mission-requirements-specialist",
              },
              { agent_name: "orchestrator", status: "running", foundry_agent_name: null },
            ],
          }),
        ],
      },
    ]);

    renderWithProviders(<DeployLaunchPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    await waitFor(() => expect(screen.getByText("Requirements Specialist")).toBeInTheDocument());
    expect(screen.getByText("local-acme-mission-requirements-specialist")).toBeInTheDocument();
    expect(screen.getByText("orchestrator")).toBeInTheDocument();
    expect(screen.getByText(/Deploying…/i)).toBeInTheDocument();
  });

  it("does not show backend deployment running while Foundry provisioning is running", async () => {
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
              {
                step_id: "provision-foundry-agents",
                name: "Deploy Agents to Foundry",
                status: "running",
                detail: "Regenerating the generated build to satisfy deterministic validation (attempt 1 of 3)...",
                error: null,
                started_at: "2026-07-23T12:00:01Z",
                completed_at: null,
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

    await waitFor(() => expect(screen.getByText(/attempt 1 of 3/i)).toBeInTheDocument());
    expect(screen.getAllByText("In Progress…")).toHaveLength(1);
    expect(screen.getAllByText("Not Started")).toHaveLength(6);
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
    expect(screen.getByRole("button", { name: /^Launch$/i })).toBeInTheDocument();
  });

  it("opens the real launch URL in a new browser tab when the Launch button is clicked", async () => {
    const launchUrl = "https://genie-i4opvs55x5qu4-swa.azurestaticapps.net/";
    mockFetchSequence([
      {
        match: "/deploy-launch/",
        response: [
          buildPipelineRun({
            status: "completed",
            launch_url: launchUrl,
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

    const windowOpenSpy = vi.spyOn(window, "open").mockImplementation(() => null);

    renderWithProviders(<DeployLaunchPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    const launchButton = await screen.findByRole("button", { name: /^Launch$/i });
    await userEvent.click(launchButton);

    expect(windowOpenSpy).toHaveBeenCalledWith(launchUrl, "_blank", "noopener,noreferrer");

    windowOpenSpy.mockRestore();
  });

  it("shows a plain error banner (no auto-retry or approval dance) when starting the pipeline fails", async () => {
    // Regression test for the reported bug: Deploy & Launch used to require
    // a "workflow_run_id" approval-checkpoint decision before it would
    // retry start() - that whole approve/decide round-trip is gone, so a
    // failed start() must simply surface its error message once.
    const fetchMock = mockFetchSequence([
      { match: "/deploy-launch/", response: [] },
      {
        match: "/deploy-launch/start",
        response: { detail: "workflow_run_id is required to approve the final output checkpoint." },
        status: 409,
      },
    ]);

    renderWithProviders(<DeployLaunchPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    await waitFor(() =>
      expect(
        screen.getByText(/workflow_run_id is required to approve the final output checkpoint/i),
      ).toBeInTheDocument(),
    );

    const startCalls = fetchMock.mock.calls.filter((call) =>
      String(call[0]).endsWith("/deploy-launch/start"),
    );
    expect(startCalls).toHaveLength(1);
    expect(fetchMock.mock.calls.some((call) => String(call[0]).endsWith("/approvals"))).toBe(false);
    expect(fetchMock.mock.calls.some((call) => String(call[0]).endsWith("/decide"))).toBe(false);
  });

  it("offers a plain retry (no forced redirect) when an earlier workflow step had not completed - the backend now self-heals by resuming the run", async () => {
    // Mirrors the real incident: build-solution's fire-and-forget kickoff
    // from Architecture Studio never reached the server, so
    // provision-foundry-agents failed with this exact backend error
    // (DeploymentPipelineService._get_step_output/UnknownWorkflowRunError).
    // DeploymentPipelineService.start() now self-heals this by resuming the
    // same workflow run before re-running the pipeline (see
    // _ensure_upstream_steps_completed), so Deploy & Launch just offers a
    // normal retry instead of forcing the user back to Workshop.
    mockFetchSequence([
      {
        match: "/deploy-launch/",
        response: [
          buildPipelineRun({
            status: "failed",
            steps: [
              {
                step_id: "generate-access-policy",
                name: "Generate Access Policy & Least Access",
                status: "completed",
                detail: "Generated least-access policy for 8 agent(s).",
                error: null,
                started_at: "2026-08-08T19:56:55Z",
                completed_at: "2026-08-08T19:56:56Z",
              },
              {
                step_id: "provision-foundry-agents",
                name: "Deploy Agents to Foundry",
                status: "failed",
                detail: "",
                error:
                  "Workflow step 'build-solution' has not completed for run " +
                  "'ce1240c7-9908-4ba7-ba19-d81cadd07513'.",
                started_at: "2026-08-08T19:56:57Z",
                completed_at: "2026-08-08T19:56:57Z",
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

    expect(await screen.findByRole("button", { name: /Retry Deploy & Launch/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Go to Workshop/i })).not.toBeInTheDocument();
  });
});
