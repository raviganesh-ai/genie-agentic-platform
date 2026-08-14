import type { WorkflowStreamEvent } from "@/types/workflowEvents";

const EVENT_LABELS: Record<WorkflowStreamEvent["event_type"], string> = {
  step_started: "started",
  step_delta: "working",
  step_completed: "completed",
  step_failed: "failed",
};

/** Longest preview snippet ever shown inline - keeps the live activity
 * banners a short, readable status line instead of a wall of text. */
const MAX_PREVIEW_LENGTH = 90;

/** Collapses a raw event preview (which for code-generation steps like
 * build-solution can be a multi-line, mid-token chunk of raw source code -
 * already whitespace-flattened to one line by the backend's own `_preview`
 * helper, so no closed fence remains to parse structurally) into a single
 * short, human-readable snippet - never a code dump. Strips markdown
 * code-fence markers and agent-comment noise, flattens all
 * whitespace/newlines to single spaces, and hard-truncates with an
 * ellipsis. `maxLength` defaults to the short inline-banner length; pass a
 * larger value (e.g. from the Triage panel's mission trace, which has more
 * room for a real output summary) for a longer snippet. */
export function sanitizePreview(preview: string, maxLength: number = MAX_PREVIEW_LENGTH): string {
  const flattened = preview
    .replace(/```[a-zA-Z0-9]*/g, " ")
    .replace(/\/\/\s*agent:\s*\S+/gi, " ")
    .replace(/#\s*agent:\s*\S+/gi, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (!flattened) return "";
  return flattened.length > maxLength ? `${flattened.slice(0, maxLength).trimEnd()}...` : flattened;
}

/** Human-readable one-liner for a single SSE workflow-events entry, shared
 * by `LiveWorkflowPulse` (persistent thin status line) and
 * `AgentActivityAnimation` (prominent waiting-state banner) so both surfaces
 * describe live agent activity identically. */
export function describeEvent(event: WorkflowStreamEvent): string {
  const label = EVENT_LABELS[event.event_type];
  const rawPreview = event.delta ?? event.output_preview ?? event.error ?? "";
  const preview = sanitizePreview(rawPreview);
  const stepLabel = event.step_id.replace(/-/g, " ");
  return preview
    ? `${event.agent_id} (${stepLabel}) ${label}: ${preview}`
    : `${event.agent_id} (${stepLabel}) ${label}`;
}
