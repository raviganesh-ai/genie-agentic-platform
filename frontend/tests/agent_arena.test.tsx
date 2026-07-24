import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders, mockFetchSequence } from "./testUtils";
import {
  buildAgentDefinitions,
  buildApprovalRequests,
  buildGovernanceEvents,
  buildMissionControlSnapshot,
  buildWorkflowRunResult,
  FIXTURE_SESSION_ID,
} from "./fixtures";
import { AgentArenaPage } from "@/features/agent-arena/AgentArenaPage";

describe("AgentArenaPage", () => {
  it("renders an Agent Command Card with a real, calculated contribution score", async () => {
    mockFetchSequence([
      { match: "/agents", response: buildAgentDefinitions() },
      { match: "/active-agents", response: { active_agents: ["requirements-analyst"], completed_agents: [], blocked_agents: [] } },
      { match: "/mission-control", response: buildMissionControlSnapshot() },
      { match: "/workflows/runs", response: [buildWorkflowRunResult()] },
      { match: "/approvals", response: buildApprovalRequests() },
      { match: "/governance/events", response: buildGovernanceEvents() },
    ]);

    renderWithProviders(<AgentArenaPage />, { sessionId: FIXTURE_SESSION_ID });

    await waitFor(() => expect(screen.getByText("Requirements Analyst")).toBeInTheDocument());
    expect(screen.getByText(/contribution points/i)).toBeInTheDocument();
  });
});
