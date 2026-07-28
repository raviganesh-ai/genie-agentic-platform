import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders, mockFetchSequence } from "./testUtils";
import { FIXTURE_SESSION_ID, FIXTURE_WORKFLOW_RUN_ID } from "./fixtures";
import { TriagePanel } from "@/features/triage/TriagePanel";
import type { GovernanceEvent } from "@/types/governance";

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
      workflow_step: true,
      output_preview: "Identified 3 goals and 2 constraints from the transcript.",
    },
    ...overrides,
  };
}

describe("TriagePanel", () => {
  it("renders nothing when triage mode is disabled", () => {
    renderWithProviders(<TriagePanel enabled={false} />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    expect(screen.queryByText(/Agent Triage/i)).not.toBeInTheDocument();
  });

  it("shows a gamified, concise live feed sourced from real agent_execution governance events", async () => {
    mockFetchSequence([
      {
        match: "/governance/events",
        response: [
          buildAgentExecutionEvent(),
          buildAgentExecutionEvent({
            id: "event-2",
            agent_id: "architecture-designer",
            detail: {
              step_id: "design-architecture",
              workflow_step: true,
              output_preview: "Recommended an Azure Container Apps based architecture.",
            },
          }),
        ],
      },
    ]);

    renderWithProviders(<TriagePanel enabled />, {
      sessionId: FIXTURE_SESSION_ID,
      workflowRunId: FIXTURE_WORKFLOW_RUN_ID,
    });

    expect(screen.getByText(/Agent Triage/i)).toBeInTheDocument();

    await waitFor(() =>
      expect(
        screen.getByText(/Identified 3 goals and 2 constraints from the transcript\./i),
      ).toBeInTheDocument(),
    );
    expect(
      screen.getByText(/Recommended an Azure Container Apps based architecture\./i),
    ).toBeInTheDocument();
    expect(screen.getByText(/step: analyze-requirements/i)).toBeInTheDocument();
    expect(screen.getByText(/step: design-architecture/i)).toBeInTheDocument();
    expect(screen.getAllByText("+10 XP")).toHaveLength(2);
    expect(screen.getByText(/Level \d+/)).toBeInTheDocument();
    expect(screen.getByText(/2 agent calls/i)).toBeInTheDocument();
  });
});

