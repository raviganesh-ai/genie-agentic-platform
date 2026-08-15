import { apiFetch } from "./httpClient";
import type { WorkflowRunResult, WorkflowStatus, WorkflowStepInput } from "@/types/workflow";

/**
 * Statuses meaning the server is no longer advancing the run on its own - it
 * finished, failed, or is waiting on a human. Anything else ("pending",
 * "running", "waiting_for_agent") means work is still in flight.
 */
const SETTLED_STATUSES: ReadonlySet<WorkflowStatus> = new Set<WorkflowStatus>([
  "completed",
  "failed",
  "blocked",
  "waiting_for_approval",
  "waiting_for_proceed",
]);

const POLL_INTERVAL_MS = 2000;
/**
 * Safety net so a run that never settles cannot leave a caller's promise
 * pending forever. Generous: a full mission legitimately takes many minutes.
 */
const MAX_WAIT_MS = 45 * 60 * 1000;
/** Consecutive poll failures tolerated before giving up (covers brief blips). */
const MAX_CONSECUTIVE_POLL_ERRORS = 5;

const delay = (ms: number): Promise<void> =>
  new Promise((resolve) => {
    setTimeout(resolve, ms);
  });

/**
 * Polls a run until it settles.
 *
 * `POST /run` and `POST /resume` return `202 Accepted` as soon as the run is
 * scheduled, because a mission routinely outlives the platform's HTTP request
 * cap - a request held open for a whole mission gets severed by the ingress,
 * which the browser then reports as a (misleading) CORS error. Waiting here
 * instead preserves the original promise contract for every caller while each
 * individual request stays short.
 */
async function waitForRunToSettle(
  sessionId: string,
  accepted: WorkflowRunResult,
): Promise<WorkflowRunResult> {
  if (SETTLED_STATUSES.has(accepted.status)) return accepted;

  const deadline = Date.now() + MAX_WAIT_MS;
  let latest = accepted;
  let consecutiveErrors = 0;

  while (Date.now() < deadline) {
    await delay(POLL_INTERVAL_MS);
    try {
      latest = await workflowApi.getRun(sessionId, accepted.workflow_run_id);
      consecutiveErrors = 0;
      if (SETTLED_STATUSES.has(latest.status)) return latest;
    } catch (err) {
      consecutiveErrors += 1;
      if (consecutiveErrors >= MAX_CONSECUTIVE_POLL_ERRORS) throw err;
    }
  }

  return latest;
}

export const workflowApi = {
  async run(
    sessionId: string,
    workflowId: string,
    traceId?: string,
  ): Promise<WorkflowRunResult> {
    const accepted = await apiFetch<WorkflowRunResult>(
      `/sessions/${sessionId}/workflows/${workflowId}/run`,
      {
        method: "POST",
        body: traceId ? { trace_id: traceId } : undefined,
      },
    );
    return waitForRunToSettle(sessionId, accepted);
  },
  async resumeRun(
    sessionId: string,
    workflowRunId: string,
    traceId?: string,
    stepInputs?: Record<string, WorkflowStepInput>,
  ): Promise<WorkflowRunResult> {
    const accepted = await apiFetch<WorkflowRunResult>(
      `/sessions/${sessionId}/workflows/runs/${workflowRunId}/resume`,
      {
        method: "POST",
        body:
          traceId || stepInputs
            ? { trace_id: traceId, step_inputs: stepInputs }
            : undefined,
      },
    );
    return waitForRunToSettle(sessionId, accepted);
  },
  getRun(sessionId: string, workflowRunId: string): Promise<WorkflowRunResult> {
    return apiFetch<WorkflowRunResult>(`/sessions/${sessionId}/workflows/runs/${workflowRunId}`);
  },
  listRuns(sessionId: string): Promise<WorkflowRunResult[]> {
    return apiFetch<WorkflowRunResult[]>(`/sessions/${sessionId}/workflows/runs`);
  },
};
