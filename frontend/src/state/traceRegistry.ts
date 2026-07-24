/**
 * Client-side trace-id registry.
 *
 * The backend's `POST /sessions/{id}/workflows/{workflow_id}/run` accepts an
 * optional `trace_id` and, when omitted, generates one internally that is
 * never returned to the caller (`WorkflowRunResult` has no `trace_id`
 * field - confirmed against backend/app/models/workflow_models.py and
 * backend/app/api/workflows.py). Shared Memory reads and Workshop actions
 * both *require* a `trace_id` query/body field, so the frontend must be the
 * party that mints it: whenever a workflow run is started or resumed, the
 * UI generates a `trace_id` itself, sends it explicitly, and remembers it
 * here keyed by `workflow_run_id` for every later call (memory reads,
 * workshop chat/challenge/reanalysis, recommendation-trace lookups) that
 * needs to reference the same trace.
 *
 * This is a documented frontend adapter for an incomplete backend contract,
 * not invented business data - the trace id itself is only ever a random
 * identifier, never a stand-in for real backend content.
 */

const traceIdByWorkflowRunId = new Map<string, string>();

export function mintTraceId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `trace-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function registerTraceId(workflowRunId: string, traceId: string): void {
  traceIdByWorkflowRunId.set(workflowRunId, traceId);
}

export function getTraceId(workflowRunId: string): string | undefined {
  return traceIdByWorkflowRunId.get(workflowRunId);
}
