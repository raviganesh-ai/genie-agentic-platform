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
          save_enabled: true,
          model_deployment_ref: "gpt-5-mini",
          status: "questioning",
          source_upload_ids: ["upload-1"],
          analyzed_upload_ids: ["upload-1"],
          analysis_revision: 1,
          personas: [
            {
              id: "jordan-lee",
              name: "Jordan Lee",
              role_or_context: "Claims reviewer",
              description: "Reviews incoming claims",
              pain_points: ["Manual evidence checks"],
              evidence_references: ["call.txt"],
              confidence_score: 0.9,
            },
          ],
          selected_persona_id: "jordan-lee",
          selected_persona_ids: ["jordan-lee"],
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

    await waitFor(() => expect(screen.getByText("Jordan Lee")).toBeInTheDocument());
    expect(screen.queryByText("Claims reviewer")).not.toBeInTheDocument();
    expect(screen.queryByText("Manual evidence checks")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "2. Select the persona of your choice" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Select personas" })).toHaveValue("Jordan Lee");
    expect(screen.getByRole("switch", { name: "Save discovery" })).toBeChecked();
    expect(screen.getByText("Based only on evidence attributable to Jordan Lee.")).toBeInTheDocument();
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

    const fetchImplementation = fetchMock.getMockImplementation();
    let releaseFirstUpload: (() => void) | undefined;
    const firstUploadGate = new Promise<void>((resolve) => {
      releaseFirstUpload = resolve;
    });
    let uploadRequestCount = 0;
    fetchMock.mockImplementation(async (input, init) => {
      if (
        new URL(input.toString()).pathname.endsWith("/uploads/transcript")
        && init?.method === "POST"
        && uploadRequestCount++ === 0
      ) {
        await firstUploadGate;
      }
      return fetchImplementation!(input, init);
    });

    await userEvent.setup().upload(fileInput, [
      new File(["first transcript"], "customer-call.txt", { type: "text/plain" }),
      new File(["second transcript"], "workshop.txt", { type: "text/plain" }),
    ]);

    expect(await screen.findByText("customer-call.txt")).toBeInTheDocument();
    expect(screen.getByText("workshop.txt")).toBeInTheDocument();
    expect(screen.getByText("Uploading and analyzing content...")).toBeInTheDocument();
    expect(screen.getByText("Waiting to upload...")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Processing 2 files..." })).toBeDisabled();

    releaseFirstUpload?.();

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
      save_enabled: false,
      model_deployment_ref: "gpt-5-mini",
      status: "created",
      source_upload_ids: [],
      analyzed_upload_ids: [],
      analysis_revision: 0,
      personas: [],
      selected_persona_id: null,
      selected_persona_ids: [],
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
    await user.click(screen.getByRole("button", { name: "Find people" }));
    await waitFor(() => {
      const createCall = fetchMock.mock.calls.find(([input, init]) =>
        new URL(input.toString()).pathname.endsWith("/sessions/session-1/discovery")
        && init?.method === "POST",
      );
      expect(JSON.parse(String(createCall?.[1]?.body))).toMatchObject({
        source_upload_ids: ["upload-completed"],
        save_enabled: false,
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

  it("selects multiple personas and runs one Discovery analysis", async () => {
    const caseState = {
      id: "discovery-1",
      session_id: "session-1",
      owner_user_id: "user-1",
      save_enabled: false,
      model_deployment_ref: "gpt-5-mini",
      status: "awaiting_persona_selection",
      source_upload_ids: ["upload-1"],
      analyzed_upload_ids: ["upload-1"],
      analysis_revision: 1,
      personas: [
        { id: "john-greeson", name: "John Greeson", pain_points: [], evidence_references: [] },
        { id: "charles-sayre", name: "Charles Sayre", pain_points: [], evidence_references: [] },
      ],
      selected_persona_id: null,
      selected_persona_ids: [],
      deep_dive_findings: [],
      gap_analysis: null,
      qa_mode: null,
      questions: [],
      proposed_solutions: [],
      selected_solution_id: null,
      build_workflow_run_id: null,
      last_error: null,
      version: 2,
      created_at: "2026-09-12T10:00:00Z",
      updated_at: "2026-09-12T10:01:00Z",
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const pathname = new URL(input.toString()).pathname;
      const method = init?.method ?? "GET";
      if (pathname.endsWith("/workflow-events/stream")) {
        return new Response(new ReadableStream({ start: (controller) => controller.close() }));
      }
      if (pathname.endsWith("/sessions/session-1/uploads")) return Response.json([]);
      if (pathname.endsWith("/sessions/session-1/discovery/save-preference") && method === "PUT") {
        return Response.json({ ...caseState, save_enabled: true, version: 3 });
      }
      if (pathname.endsWith("/sessions/session-1/discovery/persona") && method === "POST") {
        return Response.json({
          ...caseState,
          status: "awaiting_qa_mode",
          selected_persona_id: "john-greeson",
          selected_persona_ids: ["john-greeson", "charles-sayre"],
          deep_dive_findings: ["Named evidence analyzed"],
          gap_analysis: {
            known_facts: [], risks: [], contradictions: [], information_gaps: ["More detail"],
            assumptions: [], evidence_references: [], confidence_score: 0.7,
          },
          questions: [{
            id: "detail", text: "What detail is missing?", category: "context",
            status: "pending", answer: null, recommendation: null, evidence_references: [],
          }],
        });
      }
      if (pathname.endsWith("/sessions/session-1/discovery")) return Response.json(caseState);
      throw new Error(`Unexpected request: ${method} ${pathname}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithProviders(<DiscoveryPage />, { sessionId: "session-1" });

    const picker = await screen.findByRole("combobox", { name: "Select personas" });
    picker.focus();
    await user.keyboard("{ArrowDown}{Enter}{ArrowDown}{Enter}{Escape}");
    await user.click(screen.getByRole("button", { name: "Run Discovery" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([input, init]) =>
        new URL(input.toString()).pathname.endsWith("/sessions/session-1/discovery/persona")
        && init?.method === "POST",
      );
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({
        persona_ids: ["john-greeson", "charles-sayre"],
      });
    });
    expect(await screen.findByText(
      "Based only on evidence attributable to John Greeson, Charles Sayre.",
    )).toBeInTheDocument();

    await user.click(screen.getByRole("switch", { name: "Save discovery" }));
    await waitFor(() => {
      const saveCall = fetchMock.mock.calls.find(([input, init]) =>
        new URL(input.toString()).pathname.endsWith("/save-preference")
        && init?.method === "PUT",
      );
      expect(JSON.parse(String(saveCall?.[1]?.body))).toEqual({ enabled: true });
    });
  });
});