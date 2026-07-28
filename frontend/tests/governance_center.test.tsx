import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders, mockFetchSequence } from "./testUtils";
import {
  buildApprovalRequests,
  buildGovernanceEvents,
  buildWorkflowRunResult,
  FIXTURE_SESSION_ID,
  FIXTURE_WORKFLOW_RUN_ID,
} from "./fixtures";
import { GovernancePage } from "@/features/governance-center/GovernancePage";

describe("GovernancePage", () => {
  it("derives a compliance state from real approvals and governance events", async () => {
    mockFetchSequence([
      { match: "/governance/events", response: buildGovernanceEvents() },
      { match: "/approvals", response: buildApprovalRequests() },
    ]);

    renderWithProviders(<GovernancePage />, { sessionId: FIXTURE_SESSION_ID });

    await waitFor(() => expect(screen.getByText(/Overall status/i)).toBeInTheDocument());
    // one pending approval in the fixture -> "warning" compliance state
    expect(screen.getByText(/Attention Required/i)).toBeInTheDocument();
    expect(screen.getByText(/agent execution/i)).toBeInTheDocument();
  });

  it("shows the security review and an approve & deploy action when deploy-solution is pending", async () => {
    const fetchMock = mockFetchSequence([
      { match: "/governance/events", response: buildGovernanceEvents() },
      { match: "/approvals", response: buildApprovalRequests({ subject_id: "deploy-solution" }) },
      {
        match: `/workflows/runs/${FIXTURE_WORKFLOW_RUN_ID}`,
        response: buildWorkflowRunResult({
          step_results: [
            {
              step_id: "governance-review",
              agent_id: "governance-reviewer",
              status: "completed",
              output_text: "SECURITY_REVIEW: Looks good.\nGOVERNANCE_DECISION: APPROVED",
              error: null,
              started_at: "2026-07-23T10:00:00Z",
              completed_at: "2026-07-23T10:01:00Z",
            },
          ],
        }),
      },
      { match: "/decide", response: { id: "decision-1" } },
      { match: "/resume", response: { status: "running" } },
    ]);

    renderWithProviders(<GovernancePage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    await waitFor(() => expect(screen.getByText(/GOVERNANCE_DECISION: APPROVED/i)).toBeInTheDocument());
    const deployButton = screen.getByRole("button", { name: /Approve & Deploy/i });
    const user = userEvent.setup();
    await user.click(deployButton);

    await waitFor(() => {
      expect(fetchMock.mock.calls.some((call) => String(call[0]).endsWith("/decide"))).toBe(true);
      expect(fetchMock.mock.calls.some((call) => String(call[0]).endsWith("/resume"))).toBe(true);
    });
  });
});
