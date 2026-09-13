import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DiscoveryPage } from "@/features/discovery/DiscoveryPage";
import { mockFetchSequence, renderWithProviders } from "./testUtils";

describe("DiscoveryPage", () => {
  it("renders resumable persona analysis and asks consent before a recommendation", async () => {
    const print = vi.spyOn(window, "print").mockImplementation(() => undefined);
    const originalTitle = document.title;
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
          insight_sections: [{
            title: "Manual review threatens response targets",
            summary: "Jordan Lee's manual evidence checks create the primary bottleneck and make the stated SLA difficult to sustain.",
            evidence_references: ["call.txt"],
          }],
          gap_summary: "Peak processing volume and exception rates remain unknown, preventing confident capacity sizing.",
          assumption_summary: "The analysis assumes all claims arrive digitally; Jordan Lee must validate remaining intake channels.",
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
              suggested_answers: ["Under 10,000", "10,000 to 100,000"],
              status: "recommendation_offered",
              answer: null,
              recommendation: null,
              evidence_references: [],
            },
            {
              id: "retention",
              text: "How long must claim evidence be retained?",
              category: "compliance",
              suggested_answers: ["Seven years", "Ten years"],
              status: "answered",
              answer: "Seven years",
              recommendation: null,
              evidence_references: ["policy.txt"],
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
    expect(screen.getByRole("button", { name: "Export PDF" })).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Export PDF" }));
    expect(print).toHaveBeenCalledOnce();
    expect(document.title).toBe(originalTitle);
    print.mockRestore();
    expect(screen.getByText("Based only on evidence attributable to Jordan Lee.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Manual review threatens response targets" })).toBeInTheDocument();
    expect(screen.getByText(/manual evidence checks create the primary bottleneck/)).toBeInTheDocument();
    expect(screen.getByText("Evidence: call.txt")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "3. Pain points" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "4. Gaps and assumptions" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Gaps" })).toBeInTheDocument();
    expect(screen.getByText(/Peak processing volume and exception rates remain unknown/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Assumptions" })).toBeInTheDocument();
    expect(screen.getByText(/assumes all claims arrive digitally/)).toBeInTheDocument();
    expect(screen.queryByText("Manual review may breach the response target")).not.toBeInTheDocument();
    expect(screen.queryByText("The stated SLA conflicts with the manual queue")).not.toBeInTheDocument();
    expect(screen.getByText("2 questions · 1 answered")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "5. Clarify what matters" })).toBeInTheDocument();
    expect(screen.getByText("What is the peak monthly volume?")).toBeInTheDocument();
    expect(screen.queryByText("How long must claim evidence be retained?")).not.toBeInTheDocument();
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
      insight_sections: [],
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
      insight_sections: [],
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
    const analyzedCase = {
      ...caseState,
      status: "awaiting_qa_mode",
      selected_persona_id: "john-greeson",
      selected_persona_ids: ["john-greeson", "charles-sayre"],
      deep_dive_findings: ["Named evidence analyzed"],
      insight_sections: [{
        title: "Modernization priorities",
        summary: "The selected perspectives emphasize a staged modernization plan with explicit operational safeguards.",
        evidence_references: [],
      }],
      gap_analysis: {
        known_facts: [], risks: [], contradictions: [], information_gaps: ["More detail"],
        assumptions: [], evidence_references: [], confidence_score: 0.7,
      },
      questions: [{
        id: "detail",
        text: "What detail is missing?",
        category: "context",
        suggested_answers: ["Confirm the target operating model", "Confirm the migration deadline"],
        status: "pending",
        answer: null,
        recommendation: null,
        evidence_references: [],
      }],
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
        return Response.json(analyzedCase);
      }
      if (pathname.endsWith("/sessions/session-1/discovery/qa-mode") && method === "POST") {
        return Response.json({ ...analyzedCase, status: "questioning", qa_mode: "interactive" });
      }
      if (pathname.endsWith("/questions/detail/answer") && method === "POST") {
        return Response.json({ ...analyzedCase, status: "questioning", qa_mode: "interactive" });
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

    await user.click(screen.getByRole("button", { name: "One at a time" }));
    const suggestedAnswer = await screen.findByRole("radio", {
      name: "Confirm the target operating model",
    });
    await user.click(suggestedAnswer);
    const answerInput = screen.getByRole("textbox", { name: "Your answer: What detail is missing?" });
    expect(answerInput).toHaveValue("Confirm the target operating model");

    await user.clear(answerInput);
    await user.type(answerInput, "The operating model needs customer confirmation");
    expect(suggestedAnswer).not.toBeChecked();
    await user.click(screen.getByRole("button", { name: "Save answer" }));
    await waitFor(() => {
      const answerCall = fetchMock.mock.calls.find(([input, init]) =>
        new URL(input.toString()).pathname.endsWith("/questions/detail/answer")
        && init?.method === "POST",
      );
      expect(JSON.parse(String(answerCall?.[1]?.body))).toEqual({
        answer: "The operating model needs customer confirmation",
      });
    });

    await user.click(screen.getByRole("switch", { name: "Save discovery" }));
    await waitFor(() => {
      const saveCall = fetchMock.mock.calls.find(([input, init]) =>
        new URL(input.toString()).pathname.endsWith("/save-preference")
        && init?.method === "PUT",
      );
      expect(JSON.parse(String(saveCall?.[1]?.body))).toEqual({ enabled: true });
    });
  });

  it("skips the Q&A mode choice when no clarification is needed", async () => {
    const caseState = {
      id: "discovery-1",
      session_id: "session-1",
      owner_user_id: "user-1",
      save_enabled: false,
      model_deployment_ref: "gpt-5-mini",
      status: "ready_for_solutions",
      source_upload_ids: ["upload-1"],
      analyzed_upload_ids: ["upload-1"],
      analysis_revision: 1,
      personas: [{ id: "jordan-lee", name: "Jordan Lee", pain_points: [], evidence_references: [] }],
      selected_persona_id: "jordan-lee",
      selected_persona_ids: ["jordan-lee"],
      deep_dive_findings: ["The evidence resolves the material decisions"],
      insight_sections: [{
        title: "Decision context is complete",
        summary: "The available evidence resolves the material implementation decisions.",
        evidence_references: ["call.txt"],
      }],
      gap_analysis: {
        known_facts: ["Scope is approved"], risks: [], contradictions: [], information_gaps: [],
        assumptions: [], evidence_references: ["call.txt"], confidence_score: 0.95,
      },
      qa_mode: null,
      questions: [],
      proposed_solutions: [],
      selected_solution_id: null,
      build_workflow_run_id: null,
      last_error: null,
      version: 3,
      created_at: "2026-09-12T10:00:00Z",
      updated_at: "2026-09-12T10:01:00Z",
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const pathname = new URL(input.toString()).pathname;
      if (pathname.endsWith("/workflow-events/stream")) {
        return new Response(new ReadableStream({ start: (controller) => controller.close() }));
      }
      if (pathname.endsWith("/sessions/session-1/uploads")) return Response.json([]);
      if (pathname.endsWith("/sessions/session-1/discovery")) return Response.json(caseState);
      throw new Error(`Unexpected request: ${pathname}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(<DiscoveryPage />, { sessionId: "session-1" });

    expect(await screen.findByText("No additional clarification is needed for this evidence.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "One at a time" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show all questions" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate probable solutions" })).toBeInTheDocument();
  });

  it("renders probable solutions as an Azure service architecture", async () => {
    const caseState = {
      id: "discovery-1",
      session_id: "session-1",
      owner_user_id: "user-1",
      save_enabled: false,
      model_deployment_ref: "gpt-5-mini",
      status: "awaiting_solution_selection",
      source_upload_ids: ["upload-1"],
      analyzed_upload_ids: ["upload-1"],
      analysis_revision: 1,
      personas: [],
      selected_persona_id: null,
      selected_persona_ids: [],
      deep_dive_findings: [],
      insight_sections: [],
      gap_summary: "",
      assumption_summary: "",
      gap_analysis: null,
      qa_mode: null,
      questions: [],
      proposed_solutions: [{
        id: "solution-1",
        name: "Intelligent document processing",
        summary: "A managed Azure architecture for secure document intake and analysis.",
        requirements_text: "Process uploaded documents securely.",
        architecture_text: "Static Web Apps connects to API Management and Azure AI Foundry.",
        architecture_nodes: [
          {
            id: "web",
            service_name: "Azure Static Web Apps",
            azure_icon_key: "azure static web apps",
            purpose: "Hosts the customer application.",
            x: 0,
            y: 0,
          },
          {
            id: "api",
            service_name: "Azure API Management",
            azure_icon_key: "azure api management",
            purpose: "Secures and routes service requests.",
            x: 0,
            y: 0,
          },
          {
            id: "ai",
            service_name: "Azure AI Foundry",
            azure_icon_key: "azure ai foundry",
            purpose: "Orchestrates governed AI agents.",
            x: 0,
            y: 0,
          },
        ],
        architecture_edges: [
          { id: "web-api", source: "web", target: "api", label: "HTTPS" },
          { id: "api-ai", source: "api", target: "ai", label: "Invokes" },
        ],
        pros: ["Managed Azure services"],
        cons: ["Requires cloud connectivity"],
        ai_feasibility: "recommended",
        ai_feasibility_rationale: "The workflow maps to managed Azure AI capabilities.",
        evidence_references: ["customer-call.txt"],
        pricing_queries: [
          {
            service_name: "Azure API Management",
            arm_region_name: "eastus",
            sku_name: "Consumption",
            units_per_month: 100000,
            assumption: "100,000 API calls per month",
          },
          {
            service_name: "Azure AI Foundry",
            arm_region_name: "eastus",
            sku_name: null,
            units_per_month: 1,
            assumption: "One billable model deployment",
          },
        ],
        cost_estimate: {
          currency_code: "USD",
          region: "eastus",
          monthly_amount: 125,
          annual_amount: 1500,
          coverage: "partial",
          assumptions: ["100,000 API calls per month", "One billable model deployment"],
          source_urls: ["https://prices.azure.com/api/retail/prices"],
          retrieved_at: "2026-09-12T10:00:00Z",
        },
      }],
      selected_solution_id: null,
      build_workflow_run_id: null,
      last_error: null,
      version: 4,
      created_at: "2026-09-12T10:00:00Z",
      updated_at: "2026-09-12T10:02:00Z",
    };
    const fetchMock = mockFetchSequence([
      { match: "/sessions/session-1/discovery", response: caseState },
      { match: "/sessions/session-1/uploads", response: [] },
    ]);

    renderWithProviders(<DiscoveryPage />, { sessionId: "session-1" });

    expect(await screen.findByRole("heading", { name: "6. Probable solutions" })).toBeInTheDocument();
    expect(screen.getByRole("img", {
      name: "Intelligent document processing Azure service architecture",
    })).toBeInTheDocument();
    expect(screen.getByText("3 Azure services")).toBeInTheDocument();
    expect(screen.getByText("Azure Static Web Apps")).toBeInTheDocument();
    expect(screen.getAllByText("Azure API Management")).toHaveLength(2);
    expect(screen.getAllByText("Azure AI Foundry")).toHaveLength(2);
    const cost = screen.getByRole("region", { name: "Estimated Azure solution cost" });
    expect(within(cost).getByText("$125.00")).toBeInTheDocument();
    expect(within(cost).getByText("$1,500.00")).toBeInTheDocument();
    expect(within(cost).getByText("Based on 2 pricing inputs from this architecture in eastus.")).toBeInTheDocument();
    expect(within(cost).getByText("Consumption · 100,000 billable units/month")).toBeInTheDocument();
    expect(within(cost).getByText("100,000 API calls per month")).toBeInTheDocument();
    expect(within(cost).getByText(/implementation, support, taxes, and negotiated discounts are excluded/)).toBeInTheDocument();
    expect(screen.queryByText("Estimated Azure run rate")).not.toBeInTheDocument();

    await userEvent.setup().click(screen.getByRole("button", { name: "Refresh Azure pricing" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/sessions/session-1/discovery/solutions/pricing"),
      expect.objectContaining({ method: "POST" }),
    ));
  });
});