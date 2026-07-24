import { useCallback, useState } from "react";
import { workflowApi } from "@/services/workflowApi";
import { ApiError } from "@/services/httpClient";
import { mintTraceId, registerTraceId } from "@/state/traceRegistry";
import type { SafeError } from "@/types/common";
import type { WorkflowRunResult } from "@/types/workflow";

export interface WorkflowRunController {
  run: (workflowId: string) => Promise<WorkflowRunResult>;
  resume: (workflowRunId: string) => Promise<WorkflowRunResult>;
  running: boolean;
  error: SafeError | null;
}

/**
 * Starts/resumes workflow runs and always mints a client-side trace id (see
 * src/state/traceRegistry.ts) so later memory/workshop/replay lookups for
 * that run remain possible.
 */
export function useWorkflowRun(sessionId: string | null): WorkflowRunController {
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<SafeError | null>(null);

  const run = useCallback(
    async (workflowId: string): Promise<WorkflowRunResult> => {
      if (!sessionId) throw new Error("No active session");
      setRunning(true);
      setError(null);
      try {
        const traceId = mintTraceId();
        const result = await workflowApi.run(sessionId, workflowId, traceId);
        registerTraceId(result.workflow_run_id, traceId);
        return result;
      } catch (err) {
        const safe: SafeError =
          err instanceof ApiError ? err : { message: "Unable to start the workflow." };
        setError(safe);
        throw err;
      } finally {
        setRunning(false);
      }
    },
    [sessionId],
  );

  const resume = useCallback(
    async (workflowRunId: string): Promise<WorkflowRunResult> => {
      if (!sessionId) throw new Error("No active session");
      setRunning(true);
      setError(null);
      try {
        const traceId = mintTraceId();
        const result = await workflowApi.resumeRun(sessionId, workflowRunId, traceId);
        registerTraceId(workflowRunId, traceId);
        return result;
      } catch (err) {
        const safe: SafeError =
          err instanceof ApiError ? err : { message: "Unable to resume the workflow." };
        setError(safe);
        throw err;
      } finally {
        setRunning(false);
      }
    },
    [sessionId],
  );

  return { run, resume, running, error };
}
