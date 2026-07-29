import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders, mockFetchSequence } from "./testUtils";
import {
  buildApprovalRequests,
  buildGovernanceEvents,
  buildGovernanceGateReport,
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

  it("shows the security review and requires acknowledgement before deploying when every gate passes", async () => {
    const fetchMock = mockFetchSequence([
      { match: "/governance/events", response: buildGovernanceEvents() },
      { match: "/approvals", response: buildApprovalRequests({ subject_id: "deploy-solution" }) },
      { match: "/gate-report", response: buildGovernanceGateReport() },
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
    await waitFor(() => expect(screen.getByText(/Peer Review decision/i)).toBeInTheDocument());

    const deployButton = screen.getByRole("button", { name: /Proceed to Deploy/i });
    expect(deployButton).toBeDisabled();

    const user = userEvent.setup();
    await user.click(screen.getByRole("checkbox", { name: /AI can make mistakes/i }));
    expect(deployButton).toBeEnabled();
    await user.click(deployButton);

    await waitFor(() => {
      expect(fetchMock.mock.calls.some((call) => String(call[0]).endsWith("/decide"))).toBe(true);
      expect(fetchMock.mock.calls.some((call) => String(call[0]).endsWith("/resume"))).toBe(true);
    });
  });

  it("lists Peer Review findings and applies selected fixes", async () => {
    const fetchMock = mockFetchSequence([
      { match: "/governance/events", response: buildGovernanceEvents() },
      { match: "/approvals", response: buildApprovalRequests({ subject_id: "deploy-solution" }) },
      {
        match: "/gate-report",
        response: buildGovernanceGateReport({
          security_gate: "fail",
          decision: "blocked",
          findings: [
            {
              id: "sec-1",
              gate: "security",
              severity: "critical",
              description: "SQL injection in the search endpoint",
              recommendation: "Use parameterized queries.",
            },
          ],
        }),
      },
      {
        match: `/workflows/runs/${FIXTURE_WORKFLOW_RUN_ID}`,
        response: buildWorkflowRunResult({
          step_results: [
            {
              step_id: "governance-review",
              agent_id: "governance-reviewer",
              status: "completed",
              output_text: "SECURITY_REVIEW: Needs work.\nGOVERNANCE_DECISION: REJECTED",
              error: null,
              started_at: "2026-07-23T10:00:00Z",
              completed_at: "2026-07-23T10:01:00Z",
            },
          ],
        }),
      },
      { match: "/fixes", response: { status: "running" } },
    ]);

    renderWithProviders(<GovernancePage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    await waitFor(() =>
      expect(screen.getByText(/SQL injection in the search endpoint/i)).toBeInTheDocument(),
    );

    const user = userEvent.setup();
    await user.click(screen.getByRole("checkbox", { name: /SQL injection in the search endpoint/i }));
    await user.click(screen.getByRole("button", { name: /Apply Selected Fixes/i }));

    await waitFor(() => {
      expect(fetchMock.mock.calls.some((call) => String(call[0]).endsWith("/fixes"))).toBe(true);
    });
  });

  it("requires a justified risk acceptance before deploy is enabled when Peer Review is blocked", async () => {
    const fetchMock = mockFetchSequence([
      { match: "/governance/events", response: buildGovernanceEvents() },
      { match: "/approvals", response: buildApprovalRequests({ subject_id: "deploy-solution" }) },
      {
        match: "/gate-report",
        response: buildGovernanceGateReport({ security_gate: "fail", decision: "blocked" }),
      },
      {
        match: `/workflows/runs/${FIXTURE_WORKFLOW_RUN_ID}`,
        response: buildWorkflowRunResult({
          step_results: [
            {
              step_id: "governance-review",
              agent_id: "governance-reviewer",
              status: "completed",
              output_text: "SECURITY_REVIEW: Needs work.\nGOVERNANCE_DECISION: REJECTED",
              error: null,
              started_at: "2026-07-23T10:00:00Z",
              completed_at: "2026-07-23T10:01:00Z",
            },
          ],
        }),
      },
      { match: "/risk-acceptance", response: { id: "event-risk-1", category: "risk_accepted" } },
    ]);

    renderWithProviders(<GovernancePage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    await waitFor(() => expect(screen.getByText(/Peer Review has blocked this build/i)).toBeInTheDocument());

    const acceptButton = screen.getByRole("button", { name: /Accept Risk & Unlock Deploy/i });
    expect(acceptButton).toBeDisabled();

    const user = userEvent.setup();
    await user.type(
      screen.getByPlaceholderText(/Justify why it is acceptable/i),
      "Accepted for the prototype demo.",
    );
    expect(acceptButton).toBeEnabled();
    await user.click(acceptButton);

    await waitFor(() => {
      expect(fetchMock.mock.calls.some((call) => String(call[0]).endsWith("/risk-acceptance"))).toBe(true);
    });
  });
});
