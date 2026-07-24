import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders, mockFetchSequence } from "./testUtils";
import { buildSessionReplayResponse, FIXTURE_SESSION_ID } from "./fixtures";
import { ReplayCenterPage } from "@/features/replay-center/ReplayCenterPage";

describe("ReplayCenterPage", () => {
  it("merges governance events and approval audit records into a chronological timeline", async () => {
    mockFetchSequence([{ match: "/replay", response: buildSessionReplayResponse() }]);

    renderWithProviders(<ReplayCenterPage />, { sessionId: FIXTURE_SESSION_ID });

    await waitFor(() => expect(screen.getByText(/Replay-Ready Timeline/i)).toBeInTheDocument());
    expect(screen.getByText(/agent execution/i)).toBeInTheDocument();
    expect(screen.getByText(/requested by architecture-designer/i)).toBeInTheDocument();
  });
});
