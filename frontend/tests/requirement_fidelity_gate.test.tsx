import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders, mockFetchSequence } from "./testUtils";
import { FIXTURE_SESSION_ID, FIXTURE_WORKFLOW_RUN_ID } from "./fixtures";
import { RequirementFidelityGatePage } from "@/features/requirement-fidelity/RequirementFidelityGatePage";
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

describe("RequirementFidelityGatePage", () => {
  it("renders inline (no popup) showing the real requirement coverage/passing evidence for the latest run", async () => {
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

    renderWithProviders(<RequirementFidelityGatePage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    expect(await screen.findByText("REQ-002")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByText(/Launch blocked by requirement gaps/i)).toBeInTheDocument();
  });

  it("shows a graceful message instead of a report when no run has started yet", async () => {
    mockFetchSequence([{ match: "/deploy-launch/", response: [] }]);

    renderWithProviders(<RequirementFidelityGatePage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    await waitFor(() => {
      expect(
        screen.getByText(/No Deploy & Launch run has been started for this mission yet/i),
      ).toBeInTheDocument();
    });
  });
});
