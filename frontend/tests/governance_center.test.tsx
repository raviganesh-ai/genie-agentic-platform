import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders, mockFetchSequence } from "./testUtils";
import {
  buildAgentAssessmentsReport,
  buildApprovalRequests,
  buildGovernanceEvents,
  buildGovernanceGateReport,
  buildServicePolicy,
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
      { match: "/service-policy", response: buildServicePolicy() },
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
      { match: "/service-policy", response: buildServicePolicy() },
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
      { match: "/service-policy", response: buildServicePolicy() },
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

  it("shows Overall status as still Reviewing while governance-review has not completed, even though earlier steps were approved", async () => {
    mockFetchSequence([
      { match: "/governance/events", response: buildGovernanceEvents() },
      // Every earlier checkpoint is already approved - no pending, rejected,
      // or expired approval exists - but the gate report has not been
      // produced yet (governance-review step still running).
      { match: "/approvals", response: buildApprovalRequests({ status: "approved" }) },
      { match: "/gate-report", response: buildGovernanceGateReport({ status: "pending", decision: null }) },
      { match: "/agent-assessments", response: buildAgentAssessmentsReport() },
      { match: "/service-policy", response: buildServicePolicy({ status: "pending" }) },
    ]);

    renderWithProviders(<GovernancePage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    await waitFor(() => expect(screen.getByText(/Overall status/i)).toBeInTheDocument());
    // Must NOT prematurely claim "Compliant" while the real governance
    // review verdict is still pending.
    expect(screen.getByText(/Reviewing/i)).toBeInTheDocument();
    expect(screen.queryByText(/^Compliant$/i)).not.toBeInTheDocument();
  });

  it("shows the Security Assessment section's findings and fix option as soon as that agent's step completes, without waiting for governance-review", async () => {
    const fetchMock = mockFetchSequence([
      { match: "/governance/events", response: buildGovernanceEvents() },
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
      { match: "/service-policy", response: buildServicePolicy({ status: "pending" }) },
    ]);

    renderWithProviders(<GovernancePage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    await waitFor(() =>
      expect(screen.getByText(/SQL injection in the search endpoint/i)).toBeInTheDocument(),
    );
    expect(screen.getByText(/Security Gate: ❌ FAIL/i)).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole("checkbox", { name: /SQL injection in the search endpoint/i }));
    await user.click(screen.getByRole("button", { name: /Apply Selected Fixes/i }));

    await waitFor(() => {
      expect(fetchMock.mock.calls.some((call) => String(call[0]).endsWith("/fixes"))).toBe(true);
    });
  });

  it("shows the Test Coverage section with the tests-generated count once test-generation completes", async () => {
    mockFetchSequence([
      { match: "/governance/events", response: buildGovernanceEvents() },
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
      { match: "/service-policy", response: buildServicePolicy() },
    ]);

    renderWithProviders(<GovernancePage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    await waitFor(() => expect(screen.getByText(/Test Coverage Gate: ✅ PASS/i)).toBeInTheDocument());
    // Both the Security Assessment and Test Coverage sections report no
    // findings in this fixture.
    expect(screen.getAllByText(/No findings raised/i)).toHaveLength(2);
  });

  it("shows a waiting animation for Service Policy until the governance review completes", async () => {
    mockFetchSequence([
      { match: "/governance/events", response: buildGovernanceEvents() },
      { match: "/approvals", response: buildApprovalRequests({ status: "approved" }) },
      { match: "/gate-report", response: buildGovernanceGateReport({ status: "pending", decision: null }) },
      { match: "/agent-assessments", response: buildAgentAssessmentsReport() },
      { match: "/service-policy", response: buildServicePolicy({ status: "pending" }) },
    ]);

    renderWithProviders(<GovernancePage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    await waitFor(() => expect(screen.getByText(/🔐 Service Policy/i)).toBeInTheDocument());
    expect(
      screen.getByText(/Genie is working with the Governance Reviewer agent to determine the access control policy/i),
    ).toBeInTheDocument();
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
      { match: "/governance/events", response: buildGovernanceEvents() },
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
      { match: "/service-policy", response: buildServicePolicy({ status: "pending" }) },
      {
        match: "/workflow-events/stream",
        sseChunks: [
          `data: ${JSON.stringify(deltaEvent("Reviewing authentication and input handling"))}\n\n`,
          `data: ${JSON.stringify(deltaEvent(" for injection risks..."))}\n\n`,
        ],
      },
    ]);

    renderWithProviders(<GovernancePage />, {
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

  it("shows the real deployment checkpoint statuses, enforced policy documents, and access control assessment once Service Policy is ready", async () => {
    mockFetchSequence([
      { match: "/governance/events", response: buildGovernanceEvents() },
      { match: "/approvals", response: buildApprovalRequests({ status: "approved" }) },
      { match: "/gate-report", response: buildGovernanceGateReport({ status: "pending", decision: null }) },
      { match: "/agent-assessments", response: buildAgentAssessmentsReport() },
      { match: "/service-policy", response: buildServicePolicy() },
    ]);

    renderWithProviders(<GovernancePage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    await waitFor(() => expect(screen.getByText(/Build Review Approval/i)).toBeInTheDocument());
    expect(screen.getByText(/✅ Approved/i)).toBeInTheDocument();
    expect(screen.getByText(/Final Output Approval/i)).toBeInTheDocument();
    expect(screen.getByText(/— Not reached yet/i)).toBeInTheDocument();
    expect(screen.getByText(/Personal Agent Memory — accessible by: owning_agent/i)).toBeInTheDocument();
    expect(screen.getByText(/Least-privilege roles only; no secrets embedded\./i)).toBeInTheDocument();
    expect(screen.getByText(/governance-reviewer/i)).toBeInTheDocument();
  });
});
