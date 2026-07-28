import type { WorkflowStreamEvent } from "@/types/workflowEvents";

const EVENT_LABELS: Record<WorkflowStreamEvent["event_type"], string> = {
  step_started: "started",
  step_delta: "working",
  step_completed: "completed",
  step_failed: "failed",
};

/** Human-readable one-liner for a single SSE workflow-events entry, shared
 * by `LiveWorkflowPulse` (persistent thin status line) and
 * `AgentActivityAnimation` (prominent waiting-state banner) so both surfaces
 * describe live agent activity identically. */
export function describeEvent(event: WorkflowStreamEvent): string {
  const label = EVENT_LABELS[event.event_type];
  const preview = event.delta ?? event.output_preview ?? event.error ?? "";
  const stepLabel = event.step_id.replace(/-/g, " ");
  return preview
    ? `${event.agent_id} (${stepLabel}) ${label}: ${preview}`
    : `${event.agent_id} (${stepLabel}) ${label}`;
}
