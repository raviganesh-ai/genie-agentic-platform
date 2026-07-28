import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { LiveWorkflowPulse } from "@/components/LiveWorkflowPulse";
import type { WorkflowStreamEvent } from "@/types/workflowEvents";

function buildEvent(overrides: Partial<WorkflowStreamEvent> = {}): WorkflowStreamEvent {
  return {
    event_type: "step_completed",
    session_id: "session-1",
    workflow_run_id: "run-1",
    step_id: "design-architecture",
    agent_id: "architecture-designer",
    delta: null,
    output_preview: "Recommended a serverless design.",
    error: null,
    emitted_at: "2026-07-28T00:00:00Z",
    ...overrides,
  };
}

describe("LiveWorkflowPulse", () => {
  it("renders nothing when not connected and no events have arrived", () => {
    const { container } = render(<LiveWorkflowPulse connected={false} events={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows a generic live indicator once connected with no events yet", () => {
    render(<LiveWorkflowPulse connected events={[]} />);
    expect(screen.getByText(/watching for workflow activity/i)).toBeInTheDocument();
  });

  it("describes the most recent event", () => {
    render(<LiveWorkflowPulse connected events={[buildEvent()]} />);
    expect(
      screen.getByText(
        /architecture-designer \(design architecture\) completed: Recommended a serverless design\./i,
      ),
    ).toBeInTheDocument();
  });
});
