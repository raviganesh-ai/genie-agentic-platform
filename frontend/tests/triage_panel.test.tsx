import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import { renderWithProviders, mockFetchSequence } from "./testUtils";
import { FIXTURE_SESSION_ID, FIXTURE_WORKFLOW_RUN_ID } from "./fixtures";
import { TriagePanel } from "@/features/triage/TriagePanel";
import type { GovernanceEvent } from "@/types/governance";
import type { WorkflowStreamEvent } from "@/types/workflowEvents";

function buildAgentExecutionEvent(overrides: Partial<GovernanceEvent> = {}): GovernanceEvent {
  return {
    id: "event-1",
    category: "agent_execution",
    session_id: FIXTURE_SESSION_ID,
    trace_id: "trace-1",
    agent_id: "requirements-analyst",
    timestamp: new Date().toISOString(),
    detail: {
      step_id: "analyze-requirements",
      workflow_step: false,
      output_preview: "Identified 3 goals and 2 constraints from the transcript.",
    },
    ...overrides,
  };
}

function buildStreamEvent(overrides: Partial<WorkflowStreamEvent> = {}): WorkflowStreamEvent {
  return {
    event_type: "step_started",
    session_id: FIXTURE_SESSION_ID,
    workflow_run_id: FIXTURE_WORKFLOW_RUN_ID,
    step_id: "build-solution",
    agent_id: "build-agent",
    delta: null,
    output_preview: null,
    error: null,
    emitted_at: new Date().toISOString(),
    ...overrides,
  };
}

function sseFrame(event: WorkflowStreamEvent): string {
  return `data: ${JSON.stringify(event)}\n\n`;
}

describe("TriagePanel", () => {
  it("renders nothing when triage mode is disabled", () => {
    renderWithProviders(<TriagePanel enabled={false} />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    expect(screen.queryByText(/Mission Trace/i)).not.toBeInTheDocument();
  });

  it("shows completed phases from persisted governance events with a human-proceed gate before a gated phase", async () => {
    mockFetchSequence([
      {
        match: "/peer-review/events",
        response: [
          buildAgentExecutionEvent(),
          buildAgentExecutionEvent({
            id: "event-2",
            agent_id: "architecture-designer",
            detail: {
              step_id: "design-architecture",
              workflow_step: false,
              output_preview: "Recommended an Azure Container Apps based architecture.",
            },
          }),
        ],
      },
    ]);

    renderWithProviders(<TriagePanel enabled />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
      missionStartedAt: Date.now() - 5000,
    });

    expect(screen.getByText(/Mission Trace/i)).toBeInTheDocument();

    await waitFor(() =>
      expect(
        screen.getByText(/Identified 3 goals and 2 constraints from the transcript\./i),
      ).toBeInTheDocument(),
    );
    expect(
      screen.getByText(/Recommended an Azure Container Apps based architecture\./i),
    ).toBeInTheDocument();
    // design-architecture requires human proceed - since it already
    // completed, the gate must show as cleared, not as still awaiting.
    expect(screen.getByText(/Human: proceeded/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Completed/i).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/delegating to architecture-designer/i)).toBeInTheDocument();
  });

  it("shows a phase as live 'Running' the instant its SSE step_started event arrives", async () => {
    mockFetchSequence([
      { match: "/peer-review/events", response: [] },
      {
        match: "/workflow-events/stream",
        sseChunks: [sseFrame(buildStreamEvent({ step_id: "build-solution", agent_id: "build-agent" }))],
      },
    ]);

    renderWithProviders(<TriagePanel enabled />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
      missionStartedAt: Date.now() - 5000,
    });

    await waitFor(() => expect(screen.getByText(/Running/i)).toBeInTheDocument());
    expect(screen.getByText(/delegating to build-agent/i)).toBeInTheDocument();
  });

  it("does not regress a durable completion to Running for an older replayed SSE start", async () => {
    const completedAt = new Date();
    const staleStartedAt = new Date(completedAt.getTime() - 60_000);
    mockFetchSequence([
      {
        match: "/peer-review/events",
        response: [
          buildAgentExecutionEvent({
            agent_id: "build-agent",
            timestamp: completedAt.toISOString(),
            detail: {
              step_id: "build-solution",
              workflow_step: false,
              output_preview: "Generated the mission UI and agent workflow.",
            },
          }),
        ],
      },
      {
        match: "/workflow-events/stream",
        sseChunks: [
          sseFrame(
            buildStreamEvent({
              step_id: "build-solution",
              agent_id: "build-agent",
              emitted_at: staleStartedAt.toISOString(),
            }),
          ),
        ],
      },
    ]);

    renderWithProviders(<TriagePanel enabled />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
      missionStartedAt: Date.now() - 5000,
    });

    const buildHeading = await screen.findByText(/Build \(UI & Agent Workflow\)/i);
    const buildCard = buildHeading.closest(".genie-fade-in");
    expect(buildCard).not.toBeNull();
    expect(within(buildCard as HTMLElement).getByText(/Completed/i)).toBeInTheDocument();
    expect(within(buildCard as HTMLElement).queryByText(/^Running$/i)).not.toBeInTheDocument();
    expect(screen.getByText(/Generated the mission UI and agent workflow\./i)).toBeInTheDocument();
  });

  it("shows Running when an SSE retry starts after the durable completion", async () => {
    const completedAt = new Date();
    const retryStartedAt = new Date(completedAt.getTime() + 60_000);
    mockFetchSequence([
      {
        match: "/peer-review/events",
        response: [
          buildAgentExecutionEvent({
            agent_id: "build-agent",
            timestamp: completedAt.toISOString(),
            detail: {
              step_id: "build-solution",
              workflow_step: false,
              output_preview: "Generated the prior mission build.",
            },
          }),
        ],
      },
      {
        match: "/workflow-events/stream",
        sseChunks: [
          sseFrame(
            buildStreamEvent({
              step_id: "build-solution",
              agent_id: "build-agent",
              emitted_at: retryStartedAt.toISOString(),
            }),
          ),
        ],
      },
    ]);

    renderWithProviders(<TriagePanel enabled />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
      missionStartedAt: Date.now() - 5000,
    });

    const buildHeading = await screen.findByText(/Build \(UI & Agent Workflow\)/i);
    const buildCard = buildHeading.closest(".genie-fade-in");
    expect(buildCard).not.toBeNull();
    expect(within(buildCard as HTMLElement).getByText(/^Running$/i)).toBeInTheDocument();
    expect(screen.queryByText(/Generated the prior mission build\./i)).not.toBeInTheDocument();
  });

  it("shows Build as proceeded immediately after architecture approval navigates to Workshop", async () => {
    mockFetchSequence([
      {
        match: "/peer-review/events",
        response: [
          buildAgentExecutionEvent(),
          buildAgentExecutionEvent({
            id: "event-2",
            agent_id: "architecture-designer",
            detail: {
              step_id: "design-architecture",
              workflow_step: false,
              output_preview: "Recommended an Azure Container Apps based architecture.",
            },
          }),
        ],
      },
    ]);

    renderWithProviders(<TriagePanel enabled />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
      missionStartedAt: Date.now() - 5000,
      route: "/workshop",
    });

    await waitFor(() => expect(screen.getAllByText(/Human: proceeded/i)).toHaveLength(2));
    expect(screen.queryByText(/Awaiting your proceed/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Awaiting your review to proceed/i)).not.toBeInTheDocument();
  });

  it("shows a phase's real error message the instant its SSE step_failed event arrives", async () => {
    mockFetchSequence([
      { match: "/peer-review/events", response: [] },
      {
        match: "/workflow-events/stream",
        sseChunks: [
          sseFrame(
            buildStreamEvent({
              event_type: "step_failed",
              step_id: "build-solution",
              agent_id: "build-agent",
              error: "Foundry service is temporarily unavailable.",
            }),
          ),
        ],
      },
    ]);

    renderWithProviders(<TriagePanel enabled />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
      missionStartedAt: Date.now() - 5000,
    });

    await waitFor(() =>
      expect(screen.getByText(/Foundry service is temporarily unavailable\./i)).toBeInTheDocument(),
    );
    expect(screen.getByText(/❌ A phase failed/i)).toBeInTheDocument();
  });
});


