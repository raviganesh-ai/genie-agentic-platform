import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders, mockFetchSequence } from "./testUtils";
import {
  buildAgentAssessmentsReport,
  buildApprovalRequests,
  buildGovernanceEvents,
  buildGovernanceGateReport,
  buildWorkflowRunResult,
  FIXTURE_SESSION_ID,
  FIXTURE_WORKFLOW_RUN_ID,
} from "./fixtures";
import { PeerReviewPage } from "@/features/peer-review/PeerReviewPage";

describe("PeerReviewPage", () => {
  it("derives a compliance state from real approvals and governance events", async () => {
    mockFetchSequence([
      { match: "/peer-review/events", response: buildGovernanceEvents() },
      { match: "/approvals", response: buildApprovalRequests() },
    ]);

    renderWithProviders(<PeerReviewPage />, { sessionId: FIXTURE_SESSION_ID });

    await waitFor(() => expect(screen.getByText(/Overall status/i)).toBeInTheDocument());
    // one pending approval in the fixture -> "warning" compliance state
    expect(screen.getByText(/Attention Required/i)).toBeInTheDocument();
    expect(screen.getByText(/agent execution/i)).toBeInTheDocument();
  });

  it("shows the peer review verdict and enables Proceed to Deploy & Launch once every gate passes", async () => {
    mockFetchSequence([
      { match: "/peer-review/events", response: buildGovernanceEvents() },
      { match: "/approvals", response: buildApprovalRequests({ subject_id: "build-solution" }) },
      { match: "/gate-report", response: buildGovernanceGateReport() },
      {
        match: `/workflows/runs/${FIXTURE_WORKFLOW_RUN_ID}`,
        response: buildWorkflowRunResult({
          step_results: [
            {
              step_id: "peer-review",
              agent_id: "peer-review-agent",
              status: "completed",
              output_text: "SECURITY_REVIEW: Looks good.\nGOVERNANCE_DECISION: APPROVED",
              error: null,
              started_at: "2026-07-23T10:00:00Z",
              completed_at: "2026-07-23T10:01:00Z",
            },
          ],
        }),
      },
    ]);

    renderWithProviders(<PeerReviewPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    await waitFor(() => expect(screen.getByText(/GOVERNANCE_DECISION: APPROVED/i)).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText(/Peer Review decision/i)).toBeInTheDocument());

    const proceedButton = screen.getByRole("button", { name: /Proceed to Deploy & Launch/i });
    expect(proceedButton).toBeDisabled();

    const user = userEvent.setup();
    await user.click(screen.getByRole("checkbox", { name: /AI can make mistakes/i }));
    expect(proceedButton).toBeEnabled();
  });

  it("lists Peer Review findings and applies selected fixes", async () => {
    const fetchMock = mockFetchSequence([
      { match: "/peer-review/events", response: buildGovernanceEvents() },
      { match: "/approvals", response: buildApprovalRequests({ subject_id: "build-solution" }) },
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
              step_id: "peer-review",
              agent_id: "peer-review-agent",
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

    renderWithProviders(<PeerReviewPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    await waitFor(() =>
      expect(screen.getByText(/SQL injection in the search endpoint/i)).toBeInTheDocument(),
    );

    const user = userEvent.setup();
    await user.click(screen.getByRole("checkbox", { name: /SQL injection in the search endpoint/i }));
    await user.click(screen.getByRole("button", { name: /Auto-Fix Selected Findings/i }));

    await waitFor(() => {
      expect(fetchMock.mock.calls.some((call) => String(call[0]).endsWith("/fixes"))).toBe(true);
    });
  });

  it("requires a justified risk acceptance before Proceed to Deploy & Launch is enabled when Peer Review is blocked", async () => {
    const fetchMock = mockFetchSequence([
      { match: "/peer-review/events", response: buildGovernanceEvents() },
      { match: "/approvals", response: buildApprovalRequests({ subject_id: "build-solution" }) },
      {
        match: "/gate-report",
        response: buildGovernanceGateReport({ security_gate: "fail", decision: "blocked" }),
      },
      {
        match: `/workflows/runs/${FIXTURE_WORKFLOW_RUN_ID}`,
        response: buildWorkflowRunResult({
          step_results: [
            {
              step_id: "peer-review",
              agent_id: "peer-review-agent",
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

    renderWithProviders(<PeerReviewPage />, {
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

  it("shows Overall status as still Reviewing while peer-review has not completed, even though earlier steps were approved", async () => {
    mockFetchSequence([
      { match: "/peer-review/events", response: buildGovernanceEvents() },
      // Every earlier checkpoint is already approved - no pending, rejected,
      // or expired approval exists - but the gate report has not been
      // produced yet (peer-review step still running).
      { match: "/approvals", response: buildApprovalRequests({ status: "approved" }) },
      { match: "/gate-report", response: buildGovernanceGateReport({ status: "pending", decision: null }) },
      { match: "/agent-assessments", response: buildAgentAssessmentsReport() },
    ]);

    renderWithProviders(<PeerReviewPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    await waitFor(() => expect(screen.getByText(/Overall status/i)).toBeInTheDocument());
    // Must NOT prematurely claim "Compliant" while the real peer review
    // verdict is still pending.
    expect(screen.getByText(/Reviewing/i)).toBeInTheDocument();
    expect(screen.queryByText(/^Compliant$/i)).not.toBeInTheDocument();
  });

  it("shows the Security Assessment section's findings and fix option as soon as that agent's step completes, without waiting for peer-review", async () => {
    const fetchMock = mockFetchSequence([
      { match: "/peer-review/events", response: buildGovernanceEvents() },
      { match: "/approvals", response: buildApprovalRequests({ status: "approved" }) },
      { match: "/gate-report", response: buildGovernanceGateReport({ status: "pending", decision: null }) },
      {
        match: "/agent-assessments",
        response: buildAgentAssessmentsReport({
          security_assessment: {
            status: "reviewed",
            gate: "fail",
            summary: "SECURITY_GATE: FAIL\nFINDINGS:\n- [security|critical|sec-1] SQL injection | Recommendation: Parameterize.\n",
            findings: [
              {
                id: "sec-1",
                gate: "security",
                severity: "critical",
                description: "SQL injection in the search endpoint",
                recommendation: "Use parameterized queries.",
              },
            ],
            tests_generated: 0,
            assessed_by_agent_id: "security-assessment-agent",
          },
        }),
      },
      { match: "/fixes", response: { status: "running" } },
    ]);

    renderWithProviders(<PeerReviewPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    await waitFor(() =>
      expect(screen.getByText(/SQL injection in the search endpoint/i)).toBeInTheDocument(),
    );
    expect(screen.getByText(/Security Gate: ❌ FAIL/i)).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole("checkbox", { name: /SQL injection in the search endpoint/i }));
    await user.click(screen.getByRole("button", { name: /Auto-Fix Selected Findings/i }));

    await waitFor(() => {
      expect(fetchMock.mock.calls.some((call) => String(call[0]).endsWith("/fixes"))).toBe(true);
    });
  });

  it("shows the Test Coverage section with the tests-generated count once test-generation completes", async () => {
    mockFetchSequence([
      { match: "/peer-review/events", response: buildGovernanceEvents() },
      { match: "/approvals", response: buildApprovalRequests({ status: "approved" }) },
      { match: "/gate-report", response: buildGovernanceGateReport({ status: "pending", decision: null }) },
      {
        match: "/agent-assessments",
        response: buildAgentAssessmentsReport({
          test_generation: {
            status: "reviewed",
            gate: "pass",
            summary: "```ts\nexpect(1).toBe(1);\n```\nTEST_COVERAGE_GATE: PASS\nFINDINGS:\nNone.\n",
            findings: [],
            tests_generated: 6,
            assessed_by_agent_id: "test-generation-agent",
          },
        }),
      },
    ]);

    renderWithProviders(<PeerReviewPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    await waitFor(() => expect(screen.getByText(/Test Coverage Gate: ✅ PASS/i)).toBeInTheDocument());
    // Both the Security Assessment and Test Coverage sections report no
    // findings in this fixture.
    expect(screen.getAllByText(/No findings raised/i)).toHaveLength(2);
  });

  it("renders the Security Assessment Agent's own live streamed output before its step has completed", async () => {
    const deltaEvent = (delta: string) => ({
      event_type: "step_delta",
      session_id: FIXTURE_SESSION_ID,
      workflow_run_id: FIXTURE_WORKFLOW_RUN_ID,
      step_id: "security-assessment",
      agent_id: "security-assessment-agent",
      delta,
      output_preview: null,
      error: null,
      emitted_at: "2026-07-23T10:00:00Z",
    });

    mockFetchSequence([
      { match: "/peer-review/events", response: buildGovernanceEvents() },
      { match: "/approvals", response: buildApprovalRequests({ status: "approved" }) },
      { match: "/gate-report", response: buildGovernanceGateReport({ status: "pending", decision: null }) },
      {
        match: "/agent-assessments",
        response: buildAgentAssessmentsReport({
          security_assessment: {
            status: "pending",
            gate: null,
            summary: "",
            findings: [],
            tests_generated: 0,
            assessed_by_agent_id: null,
          },
        }),
      },
      {
        match: "/workflow-events/stream",
        sseChunks: [
          `data: ${JSON.stringify(deltaEvent("Reviewing authentication and input handling"))}\n\n`,
          `data: ${JSON.stringify(deltaEvent(" for injection risks..."))}\n\n`,
        ],
      },
    ]);

    renderWithProviders(<PeerReviewPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    await waitFor(() =>
      expect(
        screen.getByText(/Reviewing authentication and input handling for injection risks\.\.\./i),
      ).toBeInTheDocument(),
    );
    // The generic "waiting" animation copy must be replaced by the real
    // streamed content, not shown alongside it.
    expect(
      screen.queryByText(/Genie is working with the Security Assessment Agent/i),
    ).not.toBeInTheDocument();
  });
});
