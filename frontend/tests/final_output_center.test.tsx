import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders, mockFetchSequence } from "./testUtils";
import { FIXTURE_SESSION_ID, FIXTURE_WORKFLOW_RUN_ID } from "./fixtures";
import { FinalOutputPage } from "@/features/final-output-center/FinalOutputPage";
import type { DeliverablePackage } from "@/types/workflow";

describe("FinalOutputPage", () => {
  it("generates and renders a real deliverable package returned by the backend", async () => {
    const deliverable: DeliverablePackage = {
      id: "deliverable-1",
      deliverable_type: "final_output_package",
      session_id: FIXTURE_SESSION_ID,
      workflow_run_id: FIXTURE_WORKFLOW_RUN_ID,
      generated_at: "2026-07-23T11:00:00Z",
      sections: { Summary: "All requirements approved." },
    };

    mockFetchSequence([
      { match: "/outputs", response: ["final_output_package"] },
      { match: "final_output_package", response: deliverable },
    ]);

    renderWithProviders(<FinalOutputPage />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    const user = userEvent.setup();
    const generateButton = await screen.findByRole("button", { name: /Generate Final Output Package/i });
    await user.click(generateButton);

    await waitFor(() => expect(screen.getByText(/All requirements approved\./i)).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /Export JSON/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Export Markdown/i })).toBeInTheDocument();
  });
});
