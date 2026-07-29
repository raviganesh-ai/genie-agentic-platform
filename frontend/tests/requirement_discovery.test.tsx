import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders, mockFetchSequence } from "./testUtils";
import {
  buildApprovalRequests,
  buildRequirementsQualification,
  buildWorkflowRunResult,
  FIXTURE_SESSION_ID,
  FIXTURE_WORKFLOW_RUN_ID,
} from "./fixtures";
import { RequirementDiscoveryPage } from "@/features/requirement-map/RequirementDiscoveryPage";

describe("RequirementDiscoveryPage", () => {
  it("renders pending approvals when the workflow run qualifies for an agentic workflow", async () => {
    mockFetchSequence([
      { match: "/approvals", response: buildApprovalRequests() },
      {
        match: `/requirements/${FIXTURE_WORKFLOW_RUN_ID}/qualification`,
        response: buildRequirementsQualification({ status: "qualified" }),
      },
    ]);

    renderWithProviders(<RequirementDiscoveryPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    await waitFor(() =>
      expect(screen.getByText(/workflow step · design-architecture/i)).toBeInTheDocument(),
    );
    expect(
      screen.queryByText(/doesn't currently qualify for an agentic AI workflow/i),
    ).not.toBeInTheDocument();
  });

  it("shows a graceful banner with the agent's reason when the requirements do not qualify", async () => {
    mockFetchSequence([
      { match: "/approvals", response: buildApprovalRequests() },
      {
        match: `/requirements/${FIXTURE_WORKFLOW_RUN_ID}/qualification`,
        response: buildRequirementsQualification({
          status: "not_qualified",
          reason: "This is a single deterministic lookup with no ambiguity or planning required.",
        }),
      },
    ]);

    renderWithProviders(<RequirementDiscoveryPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    await waitFor(() =>
      expect(
        screen.getByText(/doesn't currently qualify for an agentic AI workflow/i),
      ).toBeInTheDocument(),
    );
    expect(
      screen.getByText(/single deterministic lookup with no ambiguity/i),
    ).toBeInTheDocument();
  });

  it("shows the agent activity animation while a mission is in flight and no workflow_run_id has been minted yet", () => {
    // Reproduces the Upload -> Requirements navigation: Upload navigates here
    // immediately (setting missionStartedAt) before the background workflow
    // run resolves a workflow_run_id, so this page must not fall back to the
    // static "start a workflow run from Upload" empty state in that window.
    renderWithProviders(<RequirementDiscoveryPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: null,
      missionStartedAt: Date.now(),
    });

    expect(
      screen.getByText(/Genie is working with the Requirements Analyst agent/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/Start a workflow run from Upload to begin discovering requirements/i),
    ).not.toBeInTheDocument();
  });

  it("seeds an editable requirements draft and submits the edited text when approving", async () => {
    const fetchMock = mockFetchSequence([
      { match: "/approvals", response: buildApprovalRequests() },
      {
        match: `/requirements/${FIXTURE_WORKFLOW_RUN_ID}/qualification`,
        response: buildRequirementsQualification({ status: "qualified" }),
      },
      {
        match: `/workflows/runs/${FIXTURE_WORKFLOW_RUN_ID}`,
        response: buildWorkflowRunResult({
          step_results: [
            {
              step_id: "analyze-requirements",
              agent_id: "requirements-analyst",
              status: "completed",
              output_text: "1. Support SSO login\n2. Export reports as PDF",
              error: null,
              started_at: "2026-07-23T10:00:00Z",
              completed_at: "2026-07-23T10:01:00Z",
            },
          ],
        }),
      },
      { match: "/decide", response: { id: "decision-1" } },
      { match: "/resume", response: buildWorkflowRunResult({ status: "waiting_for_approval" }) },
    ]);

    renderWithProviders(<RequirementDiscoveryPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    const user = userEvent.setup();
    const editButton = await screen.findByRole("button", { name: /Edit Requirements/i });
    await user.click(editButton);

    const textbox = await screen.findByDisplayValue(/Support SSO login/i);
    await user.type(textbox, "\n3. Add audit logging");

    const approveButton = await screen.findByRole("button", { name: /^Approve$/i });
    await user.click(approveButton);

    await waitFor(() => {
      const resumeCall = fetchMock.mock.calls.find((call) =>
        String(call[0]).endsWith("/resume"),
      );
      expect(resumeCall).toBeDefined();
      const [, resumeInit] = resumeCall as unknown as [string, RequestInit];
      const body = JSON.parse(resumeInit.body as string);
      expect(body.step_inputs["design-architecture"].variables.approved_requirements).toContain(
        "Add audit logging",
      );
    });
  });
});
