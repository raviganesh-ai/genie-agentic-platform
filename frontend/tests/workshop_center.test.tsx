import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders, mockFetchSequence } from "./testUtils";
import { buildWorkflowRunResult, FIXTURE_SESSION_ID, FIXTURE_WORKFLOW_RUN_ID } from "./fixtures";
import { WorkshopPage } from "@/features/workshop-center/WorkshopPage";

describe("WorkshopPage", () => {
  it("sends a chat message and renders the real agent response returned by the backend", async () => {
    mockFetchSequence([{ match: "/workshop/chat", response: buildWorkflowRunResult() }]);

    renderWithProviders(<WorkshopPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    const user = userEvent.setup();
    const textarea = screen.getByPlaceholderText(/Ask a question or provide direction/i);
    await user.type(textarea, "What are the top risks?");
    await user.click(screen.getByRole("button", { name: /^Send$/i }));

    await waitFor(() =>
      expect(screen.getByText(/Identified 3 goals\./i)).toBeInTheDocument(),
    );
    expect(screen.getByText("requirements-analyst")).toBeInTheDocument();
  });
});
