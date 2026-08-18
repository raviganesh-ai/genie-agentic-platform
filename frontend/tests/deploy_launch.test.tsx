import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders, mockFetchSequence } from "./testUtils";
import { FIXTURE_SESSION_ID, FIXTURE_WORKFLOW_RUN_ID } from "./fixtures";
import { DeployLaunchPage } from "@/features/deploy-launch/DeployLaunchPage";
import { DEPLOYMENT_STEP_NAMES } from "@/types/deployLaunch";
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
    test_summary: null,
    fidelity_report: null,
    security_findings_count: null,
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
      for (const name of Object.values(DEPLOYMENT_STEP_NAMES)) {
        expect(screen.getByText(name)).toBeInTheDocument();
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

  it("shows candid requirement coverage and unresolved gaps before launch", async () => {
    mockFetchSequence([
      {
        match: "/deploy-launch/",
        response: [
          buildPipelineRun({
            status: "failed",
            fidelity_report: {
              status: "failed",
              requirements: [
                {
                  requirement_id: "REQ-001",
                  statement: "Process every uploaded document.",
                  status: "passed",
                  test_names: ["test_req_001_processes_every_document"],
                  evidence: "1 passed",
                },
                {
                  requirement_id: "REQ-002",
                  statement: "Export a signed result.",
                  status: "missing",
                  test_names: [],
                  evidence: "No executable acceptance test references this requirement.",
                },
              ],
              total_requirements: 2,
              covered_requirements: 1,
              passed_requirements: 1,
              coverage_percent: 50,
              pass_percent: 50,
              repair_attempts: 3,
              max_repair_attempts: 3,
              gaps: ["REQ-002: no executable acceptance test"],
              execution_summary: "1 passed, 1 uncovered",
            },
          }),
        ],
      },
    ]);

    renderWithProviders(<DeployLaunchPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    expect(await screen.findByText("Requirement Fidelity Gate")).toBeInTheDocument();
    expect(screen.getAllByText("50%")).toHaveLength(2);
    expect(screen.getByText("REQ-002")).toBeInTheDocument();
    expect(screen.getByText(/Launch blocked by requirement gaps/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Launch$/i })).not.toBeInTheDocument();
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
