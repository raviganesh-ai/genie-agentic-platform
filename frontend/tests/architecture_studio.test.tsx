import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders, mockFetchSequence } from "./testUtils";
import {
  buildApprovalRequests,
  buildArchitectureSnapshot,
  FIXTURE_SESSION_ID,
  FIXTURE_WORKFLOW_RUN_ID,
} from "./fixtures";
import { ArchitectureStudioPage } from "@/features/architecture-studio/ArchitectureStudioPage";

describe("ArchitectureStudioPage", () => {
  it("renders the requirement-derived UI design and multi-agent workflow", async () => {
    mockFetchSequence([
      { match: `/architecture/${FIXTURE_WORKFLOW_RUN_ID}`, response: buildArchitectureSnapshot() },
      { match: "/approvals", response: [] },
    ]);

    renderWithProviders(<ArchitectureStudioPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    await waitFor(() => expect(screen.getAllByText("UI").length).toBeGreaterThan(0));
    expect(screen.getAllByText("Agents").length).toBeGreaterThan(0);
    expect(screen.queryAllByText(/Azure Reference Architecture/i).length).toBe(0);
    expect(screen.getAllByText(/Document Intake Agent/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Classification Agent/i).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /Lower Cost/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Fabric-First/i })).toBeInTheDocument();
  });

  it("shows the agent activity animation while design-architecture hasn't produced a component yet", async () => {
    // Reproduces the Requirements approve -> Architecture Studio navigation:
    // the user is navigated here immediately (ahead of the background resume
    // call finishing design-architecture), so this page must show the
    // Architect agent's live activity animation rather than an empty list.
    mockFetchSequence([
      {
        match: `/architecture/${FIXTURE_WORKFLOW_RUN_ID}`,
        response: { ...buildArchitectureSnapshot(), components: [] },
      },
      { match: "/approvals", response: [] },
    ]);

    renderWithProviders(<ArchitectureStudioPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    await waitFor(() =>
      expect(screen.getByText(/Genie is working with the Architecture Designer agent/i)).toBeInTheDocument(),
    );
  });

  it("shows an error instead of the animation when the background resume failed before producing a component", async () => {
    mockFetchSequence([
      {
        match: `/architecture/${FIXTURE_WORKFLOW_RUN_ID}`,
        response: { ...buildArchitectureSnapshot(), components: [] },
      },
      { match: "/approvals", response: [] },
    ]);

    renderWithProviders(<ArchitectureStudioPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
      missionError: { message: "Failed to resume the workflow." },
    });

    await waitFor(() => expect(screen.getByText(/Failed to resume the workflow/i)).toBeInTheDocument());
    expect(
      screen.queryByText(/Genie is working with the Architecture Designer agent/i),
    ).not.toBeInTheDocument();
  });

  it("requires governance policies before approving the architecture and generating code", async () => {
    const fetchMock = mockFetchSequence([
      { match: `/architecture/${FIXTURE_WORKFLOW_RUN_ID}`, response: buildArchitectureSnapshot() },
      { match: "/approvals", response: buildApprovalRequests({ subject_id: "build-solution" }) },
      { match: "/decide", response: { id: "decision-1" } },
      { match: "/resume", response: { status: "running" } },
    ]);

    renderWithProviders(<ArchitectureStudioPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    const approveButton = await screen.findByRole("button", { name: /Approve Architecture & Generate Code/i });
    expect(approveButton).toBeDisabled();

    const user = userEvent.setup();
    const managedIdentityOption = screen.getByRole("checkbox", { name: /managed identity/i });
    await user.click(managedIdentityOption);
    expect(approveButton).toBeEnabled();

    await user.click(approveButton);

    await waitFor(() => {
      const resumeCall = fetchMock.mock.calls.find((call) => String(call[0]).endsWith("/resume"));
      expect(resumeCall).toBeDefined();
      const [, resumeInit] = resumeCall as unknown as [string, RequestInit];
      const body = JSON.parse(resumeInit.body as string);
      expect(body.step_inputs["build-solution"].variables.policies).toContain(
        "managed identity",
      );
    });
  });

  it("lets the user select 'Other' and type a custom governance policy", async () => {
    const fetchMock = mockFetchSequence([
      { match: `/architecture/${FIXTURE_WORKFLOW_RUN_ID}`, response: buildArchitectureSnapshot() },
      { match: "/approvals", response: buildApprovalRequests({ subject_id: "build-solution" }) },
      { match: "/decide", response: { id: "decision-1" } },
      { match: "/resume", response: { status: "running" } },
    ]);

    renderWithProviders(<ArchitectureStudioPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    const approveButton = await screen.findByRole("button", { name: /Approve Architecture & Generate Code/i });
    expect(approveButton).toBeDisabled();

    const user = userEvent.setup();
    const otherOption = screen.getByRole("checkbox", { name: /^Other$/i });
    await user.click(otherOption);
    const otherBox = await screen.findByPlaceholderText(/additional policy/i);
    await user.type(otherBox, "Must comply with HIPAA.");
    expect(approveButton).toBeEnabled();

    await user.click(approveButton);

    await waitFor(() => {
      const resumeCall = fetchMock.mock.calls.find((call) => String(call[0]).endsWith("/resume"));
      expect(resumeCall).toBeDefined();
      const [, resumeInit] = resumeCall as unknown as [string, RequestInit];
      const body = JSON.parse(resumeInit.body as string);
      expect(body.step_inputs["build-solution"].variables.policies).toContain("HIPAA");
    });
  });
});
