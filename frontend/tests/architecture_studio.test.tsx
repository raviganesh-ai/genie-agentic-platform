import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders, mockFetchSequence } from "./testUtils";
import { buildArchitectureSnapshot, FIXTURE_SESSION_ID, FIXTURE_WORKFLOW_RUN_ID } from "./fixtures";
import { ArchitectureStudioPage } from "@/features/architecture-studio/ArchitectureStudioPage";

describe("ArchitectureStudioPage", () => {
  it("renders recommended architecture components and reanalysis actions", async () => {
    mockFetchSequence([
      { match: `/architecture/${FIXTURE_WORKFLOW_RUN_ID}`, response: buildArchitectureSnapshot() },
    ]);

    renderWithProviders(<ArchitectureStudioPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    await waitFor(() => expect(screen.getByText(/Use Azure Container Apps/i)).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /Lower Cost/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Fabric-First/i })).toBeInTheDocument();
  });
});
