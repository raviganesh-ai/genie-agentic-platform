import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders, mockFetchSequence } from "./testUtils";
import { buildMissionControlSnapshot, FIXTURE_SESSION_ID } from "./fixtures";
import { MissionControlPage } from "@/features/mission-control/MissionControlPage";

describe("MissionControlPage", () => {
  it("renders the executive scoreboard, progress, and active agents from a real snapshot", async () => {
    mockFetchSequence([
      { match: "/mission-control", response: buildMissionControlSnapshot() },
    ]);

    renderWithProviders(<MissionControlPage />, { sessionId: FIXTURE_SESSION_ID });

    await waitFor(() => expect(screen.getByText(/Mission Progress/i)).toBeInTheDocument());
    expect(screen.getByText(/42% complete/i)).toBeInTheDocument();
    expect(screen.getByText("requirements-analyst")).toBeInTheDocument();
    expect(screen.getByText(/Executive Scoreboard/i)).toBeInTheDocument();
  });
});
