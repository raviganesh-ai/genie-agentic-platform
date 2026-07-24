import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders, mockFetchSequence } from "./testUtils";
import { buildMissionControlSnapshot, FIXTURE_SESSION_ID } from "./fixtures";
import { CollaborationGraphPage } from "@/features/collaboration-graph/CollaborationGraphPage";

describe("CollaborationGraphPage", () => {
  it("renders decision graph nodes sourced from the Mission Control snapshot", async () => {
    mockFetchSequence([
      { match: "/mission-control", response: buildMissionControlSnapshot() },
    ]);

    renderWithProviders(<CollaborationGraphPage />, { sessionId: FIXTURE_SESSION_ID });

    await waitFor(() => expect(screen.getByText("Requirements Analyst")).toBeInTheDocument());
    expect(screen.getByText("Recommendation A")).toBeInTheDocument();
  });
});
