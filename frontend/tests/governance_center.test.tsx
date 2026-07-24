import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders, mockFetchSequence } from "./testUtils";
import { buildApprovalRequests, buildGovernanceEvents, FIXTURE_SESSION_ID } from "./fixtures";
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
});
