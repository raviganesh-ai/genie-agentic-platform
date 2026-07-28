/** Mirrors backend/app/models/workflow_stream_models.py 1:1. */

export type WorkflowStreamEventType = "step_started" | "step_delta" | "step_completed" | "step_failed";

/**
 * One live event about a single workflow step's execution, delivered over
 * the `GET /sessions/{session_id}/workflow-events/stream` SSE route. A pure
 * "is currently happening" signal - never a system of record.
 */
export interface WorkflowStreamEvent {
  event_type: WorkflowStreamEventType;
  session_id: string;
  workflow_run_id: string;
  step_id: string;
  agent_id: string;
  delta: string | null;
  output_preview: string | null;
  error: string | null;
  emitted_at: string;
}
