import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DiscoveryPage } from "@/features/discovery/DiscoveryPage";
import { mockFetchSequence, renderWithProviders } from "./testUtils";

describe("DiscoveryPage", () => {
  it("renders resumable persona analysis and asks consent before a recommendation", async () => {
    const fetchMock = mockFetchSequence([
      {
        match: "/sessions/session-1/discovery",
        response: {
          id: "discovery-1",
          session_id: "session-1",
          owner_user_id: "user-1",
          model_deployment_ref: "gpt-5-mini",
          status: "questioning",
          source_upload_ids: ["upload-1"],
          analyzed_upload_ids: ["upload-1"],
          analysis_revision: 1,
          personas: [
            {
              id: "reviewer",
              name: "Claims Reviewer",
              description: "Reviews incoming claims",
              pain_points: ["Manual evidence checks"],
              evidence_references: ["call.txt"],
              confidence_score: 0.9,
            },
          ],
          selected_persona_id: "reviewer",
          deep_dive_findings: ["Evidence review is the bottleneck"],
          gap_analysis: {
            known_facts: ["Claims arrive digitally"],
            information_gaps: ["Peak volume"],
            assumptions: [],
            evidence_references: ["call.txt"],
            confidence_score: 0.8,
          },
          qa_mode: "interactive",
          questions: [
            {
              id: "volume",
              text: "What is the peak monthly volume?",
              category: "capacity",
              status: "recommendation_offered",
              answer: null,
              recommendation: null,
              evidence_references: [],
            },
          ],
          proposed_solutions: [],
          selected_solution_id: null,
          build_workflow_run_id: null,
          last_error: null,
          version: 5,
          created_at: "2026-09-01T10:00:00Z",
          updated_at: "2026-09-01T10:05:00Z",
        },
      },
      {
        match: "/sessions/session-1/uploads",
        response: [],
      },
      {
        match: "/sessions/session-1/uploads/transcript",
        response: {
          id: "upload-new",
          session_id: "session-1",
          upload_type: "transcript",
          file_name: "customer-call.txt",
          content_type: "text/plain",
          size_bytes: 16,
          uploaded_by: "user-1",
          status: "received",
          detail: "",
          uploaded_at: "2026-09-01T10:06:00Z",
          updated_at: "2026-09-01T10:06:00Z",
        },
      },
    ]);

    renderWithProviders(<DiscoveryPage />, { sessionId: "session-1" });

    await waitFor(() => expect(screen.getByText("Claims Reviewer")).toBeInTheDocument());
    expect(screen.getByText("What is the peak monthly volume?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Yes, recommend" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "No, leave unanswered" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Choose files" })).toBeInTheDocument();
    expect(screen.getByText("No files uploaded yet.")).toBeInTheDocument();

    const fileInput = screen.getByLabelText("Choose customer material files");
    expect(fileInput.getAttribute("accept")).toContain(".docx");

    await userEvent.setup().upload(fileInput, [
      new File(["first transcript"], "customer-call.txt", { type: "text/plain" }),
      new File(["second transcript"], "workshop.txt", { type: "text/plain" }),
    ]);

    await waitFor(() => {
      const uploadCalls = fetchMock.mock.calls.filter(([input, init]) =>
        new URL(input.toString()).pathname.endsWith("/uploads/transcript")
        && init?.method === "POST",
      );
      expect(uploadCalls).toHaveLength(2);
    });
  });
});