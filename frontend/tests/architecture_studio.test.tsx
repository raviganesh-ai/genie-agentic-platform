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
  it("renders recommended architecture components and reanalysis actions", async () => {
    mockFetchSequence([
      { match: `/architecture/${FIXTURE_WORKFLOW_RUN_ID}`, response: buildArchitectureSnapshot() },
      { match: "/approvals", response: [] },
    ]);

    renderWithProviders(<ArchitectureStudioPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    await waitFor(() => expect(screen.getByText(/Use Azure Container Apps/i)).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /Lower Cost/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Fabric-First/i })).toBeInTheDocument();
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
    const policiesBox = screen.getByPlaceholderText(/managed identity/i);
    await user.type(policiesBox, "Must use managed identity and least privilege access.");
    expect(approveButton).toBeEnabled();

    await user.click(approveButton);

    await waitFor(() => {
      const resumeCall = fetchMock.mock.calls.find((call) => String(call[0]).endsWith("/resume"));
      expect(resumeCall).toBeDefined();
      const [, resumeInit] = resumeCall as unknown as [string, RequestInit];
      const body = JSON.parse(resumeInit.body as string);
      expect(body.step_inputs["governance-review"].variables.policies).toContain(
        "managed identity",
      );
    });
  });
});
