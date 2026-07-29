import { useCallback, useState } from "react";
import { workshopApi } from "@/services/workshopApi";
import { getTraceId, mintTraceId, registerTraceId } from "@/state/traceRegistry";
import { ApiError } from "@/services/httpClient";
import type { ReanalysisRequestType, ReanalysisResult } from "@/types/reanalysis";
import type { WorkflowRunResult } from "@/types/workflow";
import type { SafeError } from "@/types/common";

export interface WorkshopController {
  sendMessage: (message: string, agentId?: string) => Promise<WorkflowRunResult>;
  challenge: (recommendationId: string, rationale: string) => Promise<ReanalysisResult>;
  requestAlternative: (rationale: string) => Promise<ReanalysisResult>;
  submitReanalysis: (
    requestType: ReanalysisRequestType,
    recommendationId: string | null,
    rationale: string,
  ) => Promise<ReanalysisResult>;
  updatePriorities: (rationale: string) => Promise<ReanalysisResult>;
  regenerateBuild: (instruction: string) => Promise<WorkflowRunResult>;
  busy: boolean;
  error: SafeError | null;
}

/**
 * Drives the Workshop Center: chat, challenge, alternative-request,
 * reanalysis, and priority actions, all scoped to one session + workflow
 * run and reusing that run's frontend-minted trace id (see
 * src/state/traceRegistry.ts), minting one on first use if none exists yet.
 */
export function useWorkshop(
  sessionId: string | null,
  workflowRunId: string | null,
): WorkshopController {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<SafeError | null>(null);

  const resolveTraceId = useCallback((): string => {
    if (!workflowRunId) throw new Error("No active workflow run");
    const existing = getTraceId(workflowRunId);
    if (existing) return existing;
    const minted = mintTraceId();
    registerTraceId(workflowRunId, minted);
    return minted;
  }, [workflowRunId]);

  const guard = useCallback(async <T>(action: () => Promise<T>): Promise<T> => {
    setBusy(true);
    setError(null);
    try {
      return await action();
    } catch (err) {
      setError(err instanceof ApiError ? err : { message: "The workshop action failed." });
      throw err;
    } finally {
      setBusy(false);
    }
  }, []);

  const sendMessage = useCallback(
    (message: string, agentId?: string) =>
      guard(() => {
        if (!sessionId || !workflowRunId) throw new Error("No active session/workflow run");
        const traceId = resolveTraceId();
        return agentId
          ? workshopApi.chatWithAgent(sessionId, agentId, workflowRunId, message, traceId)
          : workshopApi.chatWithAllAgents(sessionId, workflowRunId, message, traceId);
      }),
    [guard, sessionId, workflowRunId, resolveTraceId],
  );

  const challenge = useCallback(
    (recommendationId: string, rationale: string) =>
      guard(() => {
        if (!sessionId || !workflowRunId) throw new Error("No active session/workflow run");
        return workshopApi.challengeRecommendation(sessionId, {
          workflow_run_id: workflowRunId,
          trace_id: resolveTraceId(),
          target_recommendation_id: recommendationId,
          rationale,
        });
      }),
    [guard, sessionId, workflowRunId, resolveTraceId],
  );

  const requestAlternative = useCallback(
    (rationale: string) =>
      guard(() => {
        if (!sessionId || !workflowRunId) throw new Error("No active session/workflow run");
        return workshopApi.requestAlternativeArchitecture(sessionId, {
          workflow_run_id: workflowRunId,
          trace_id: resolveTraceId(),
          rationale,
        });
      }),
    [guard, sessionId, workflowRunId, resolveTraceId],
  );

  const submitReanalysis = useCallback(
    (requestType: ReanalysisRequestType, recommendationId: string | null, rationale: string) =>
      guard(() => {
        if (!sessionId || !workflowRunId) throw new Error("No active session/workflow run");
        return workshopApi.submitReanalysisRequest(sessionId, {
          workflow_run_id: workflowRunId,
          trace_id: resolveTraceId(),
          target_recommendation_id: recommendationId,
          rationale,
          request_type: requestType,
        });
      }),
    [guard, sessionId, workflowRunId, resolveTraceId],
  );

  const updatePriorities = useCallback(
    (rationale: string) =>
      guard(() => {
        if (!sessionId || !workflowRunId) throw new Error("No active session/workflow run");
        return workshopApi.updatePriorities(sessionId, {
          workflow_run_id: workflowRunId,
          trace_id: resolveTraceId(),
          rationale,
        });
      }),
    [guard, sessionId, workflowRunId, resolveTraceId],
  );

  const regenerateBuild = useCallback(
    (instruction: string) =>
      guard(() => {
        if (!sessionId || !workflowRunId) throw new Error("No active session/workflow run");
        return workshopApi.regenerateBuild(sessionId, {
          workflow_run_id: workflowRunId,
          trace_id: resolveTraceId(),
          instruction,
        });
      }),
    [guard, sessionId, workflowRunId, resolveTraceId],
  );

  return {
    sendMessage,
    challenge,
    requestAlternative,
    submitReanalysis,
    updatePriorities,
    regenerateBuild,
    busy,
    error,
  };
}
