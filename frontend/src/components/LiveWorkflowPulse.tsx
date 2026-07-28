import { Text } from "@fluentui/react-components";
import type { WorkflowStreamEvent } from "@/types/workflowEvents";

const EVENT_LABELS: Record<WorkflowStreamEvent["event_type"], string> = {
  step_started: "started",
  step_delta: "working",
  step_completed: "completed",
  step_failed: "failed",
};

function describeEvent(event: WorkflowStreamEvent): string {
  const label = EVENT_LABELS[event.event_type];
  const preview = event.delta ?? event.output_preview ?? event.error ?? "";
  const stepLabel = event.step_id.replace(/-/g, " ");
  return preview
    ? `${event.agent_id} (${stepLabel}) ${label}: ${preview}`
    : `${event.agent_id} (${stepLabel}) ${label}`;
}

/**
 * A compact "live" banner shown on each Mission Control page while its
 * workflow run is active: a pulsing dot plus the most recent step event
 * received over the SSE workflow-events stream (see
 * useWorkflowEventStream). Purely a live "is currently happening" signal -
 * each page's own polled data (useAsyncResource) remains the source of
 * truth for what actually got recorded. Renders nothing until either a
 * connection is established or at least one event has been seen, so pages
 * without an active run stay unchanged.
 */
export function LiveWorkflowPulse({
  connected,
  events,
}: {
  connected: boolean;
  events: WorkflowStreamEvent[];
}): JSX.Element | null {
  if (!connected && events.length === 0) return null;
  const lastEvent = events[events.length - 1];

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12, opacity: 0.85 }}>
      <span className="genie-live-dot" aria-hidden="true" />
      <Text size={200}>
        {lastEvent ? describeEvent(lastEvent) : "Live - watching for workflow activity..."}
      </Text>
    </div>
  );
}
