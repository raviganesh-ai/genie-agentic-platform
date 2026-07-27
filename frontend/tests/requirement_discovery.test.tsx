import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders, mockFetchSequence } from "./testUtils";
import {
  buildApprovalRequests,
  buildRequirementsQualification,
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
});
