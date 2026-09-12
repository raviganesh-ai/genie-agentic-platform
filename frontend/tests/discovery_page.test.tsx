import { describe, expect, it, vi } from "vitest";
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
            risks: ["Manual review may breach the response target"],
            contradictions: ["The stated SLA conflicts with the manual queue"],
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
    expect(screen.getByText("Manual review may breach the response target")).toBeInTheDocument();
    expect(screen.getByText("The stated SLA conflicts with the manual queue")).toBeInTheDocument();
    expect(screen.getByText("What is the peak monthly volume?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Yes, recommend" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "No, leave unanswered" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Choose files" })).toBeInTheDocument();
    expect(screen.getByText("No files uploaded yet.")).toBeInTheDocument();

    const fileInput = screen.getByLabelText("Choose customer material files");
    expect(fileInput.getAttribute("accept")).toContain(".docx");
    expect(fileInput.getAttribute("accept")).toContain(".png");
    expect(fileInput.getAttribute("accept")).toContain(".xlsx");
    expect(fileInput.getAttribute("accept")).toContain(".pptx");

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

  it("counts only completed files and lets the user remove failed uploads", async () => {
    const discoveryCase = {
      id: "discovery-1",
      session_id: "session-1",
      owner_user_id: "user-1",
      model_deployment_ref: "gpt-5-mini",
      status: "created",
      source_upload_ids: [],
      analyzed_upload_ids: [],
      analysis_revision: 0,
      personas: [],
      selected_persona_id: null,
      deep_dive_findings: [],
      gap_analysis: null,
      qa_mode: null,
      questions: [],
      proposed_solutions: [],
      selected_solution_id: null,
      build_workflow_run_id: null,
      last_error: null,
      version: 1,
      created_at: "2026-09-12T10:00:00Z",
      updated_at: "2026-09-12T10:00:00Z",
    };
    let uploads = [
      {
        id: "upload-completed",
        session_id: "session-1",
        upload_type: "transcript",
        file_name: "customer.docx",
        content_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size_bytes: 100,
        uploaded_by: "user-1",
        status: "completed",
        detail: "",
        uploaded_at: "2026-09-12T10:01:00Z",
        updated_at: "2026-09-12T10:01:00Z",
      },
      {
        id: "upload-failed",
        session_id: "session-1",
        upload_type: "transcript",
        file_name: "broken.docx",
        content_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size_bytes: 50,
        uploaded_by: "user-1",
        status: "failed",
        detail: "Unable to parse DOCX 'broken.docx'.",
        uploaded_at: "2026-09-12T10:02:00Z",
        updated_at: "2026-09-12T10:02:00Z",
      },
    ];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const pathname = new URL(input.toString()).pathname;
      const method = init?.method ?? "GET";
      if (pathname.endsWith("/workflow-events/stream")) {
        return new Response(new ReadableStream({ start: (controller) => controller.close() }));
      }
      if (pathname.endsWith("/sessions/session-1/uploads/upload-failed") && method === "DELETE") {
        uploads = uploads.filter((item) => item.id !== "upload-failed");
        return new Response(null, { status: 204 });
      }
      if (pathname.endsWith("/sessions/session-1/uploads") && method === "GET") {
        return Response.json(uploads);
      }
      if (pathname.endsWith("/sessions/session-1/discovery/analyze") && method === "POST") {
        return Response.json(discoveryCase);
      }
      if (pathname.endsWith("/sessions/session-1/discovery")) {
        return Response.json(discoveryCase, { status: method === "POST" ? 201 : 200 });
      }
      throw new Error(`Unexpected request: ${method} ${pathname}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithProviders(<DiscoveryPage />, { sessionId: "session-1" });

    expect(await screen.findByText("1 source file ready")).toBeInTheDocument();
    expect(screen.getByText("Unable to parse DOCX 'broken.docx'.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Find personas" }));
    await waitFor(() => {
      const createCall = fetchMock.mock.calls.find(([input, init]) =>
        new URL(input.toString()).pathname.endsWith("/sessions/session-1/discovery")
        && init?.method === "POST",
      );
      expect(JSON.parse(String(createCall?.[1]?.body))).toMatchObject({
        source_upload_ids: ["upload-completed"],
      });
    });

    await user.click(screen.getByRole("button", { name: "Remove broken.docx" }));

    await waitFor(() => expect(screen.queryByText("broken.docx")).not.toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/sessions/session-1/uploads/upload-failed"),
      expect.objectContaining({ method: "DELETE" }),
    );
    expect(
      fetchMock.mock.calls.filter(([input, init]) =>
        new URL(input.toString()).pathname.endsWith("/sessions/session-1/discovery")
        && (init?.method ?? "GET") === "GET",
      ),
    ).toHaveLength(2);
  });
});