import { useCallback } from "react";
import { memoryApi } from "@/services/memoryApi";
import { requirementsApi } from "@/services/requirementsApi";
import { workshopApi } from "@/services/workshopApi";
import { getTraceId } from "@/state/traceRegistry";
import { useAsyncResource, type AsyncResourceState } from "./useAsyncResource";
import type { SharedMemoryRecord } from "@/types/memory";
import type { RequirementsQualification } from "@/types/requirementsQualification";

export interface RequirementItem {
  record: SharedMemoryRecord;
}

interface RequirementsData {
  items: RequirementItem[];
  traceId: string;
}

const REQUIREMENT_CLASSIFICATIONS = new Set([
  "requirement",
  "goal",
  "constraint",
  "risk",
  "assumption",
]);

const DEFAULT_POLL_MS = Number(import.meta.env.VITE_REQUIREMENTS_POLL_MS ?? 0);

/**
 * Backs the Requirement Discovery Map. Reads Shared Collaboration Memory
 * records classified as requirement/goal/constraint/risk/assumption for the
 * given workflow run. Requires a trace_id known to the frontend (see
 * src/state/traceRegistry.ts) - if the run was never started with a
 * frontend-minted trace id, this resource is unavailable (`data` stays
 * null) rather than silently guessing.
 */
export function useRequirements(
  sessionId: string | null,
  workflowRunId: string | null,
  pollIntervalMs: number = DEFAULT_POLL_MS,
): AsyncResourceState<RequirementsData> {
  const traceId = workflowRunId ? getTraceId(workflowRunId) : undefined;

  const fetcher = useCallback(async (): Promise<RequirementsData> => {
    if (!sessionId || !traceId) {
      throw new Error(
        "No trace id is available for this workflow run yet. Requirements will appear once the run has produced traceable output.",
      );
    }
    const records = await memoryApi.listShared(sessionId, traceId);
    const items = records
      .filter((record) => REQUIREMENT_CLASSIFICATIONS.has(record.classification))
      .map((record) => ({ record }));
    return { items, traceId };
  }, [sessionId, traceId]);

  return useAsyncResource(fetcher, [sessionId, workflowRunId, traceId], {
    enabled: Boolean(sessionId && traceId),
    pollIntervalMs,
  });
}

export interface RequirementActions {
  challenge: (
    sessionId: string,
    workflowRunId: string,
    traceId: string,
    recommendationId: string,
    rationale: string,
  ) => Promise<void>;
}

/**
 * Challenge action for a Requirement Discovery Map item. Approve/reject of
 * governance approvals is intentionally NOT modeled per-requirement here -
 * see RequirementDiscoveryPage's "Pending Approvals" section, which uses
 * real ApprovalRequest ids (there is no backend linkage from a shared
 * memory requirement record to a specific ApprovalRequest today).
 */
export function useRequirementActions(): RequirementActions {
  const challenge = useCallback(
    async (
      sessionId: string,
      workflowRunId: string,
      traceId: string,
      recommendationId: string,
      rationale: string,
    ) => {
      await workshopApi.challengeRecommendation(sessionId, {
        workflow_run_id: workflowRunId,
        trace_id: traceId,
        target_recommendation_id: recommendationId,
        rationale,
      });
    },
    [],
  );

  return { challenge };
}

/**
 * Fetches whether the requirements discovered for a workflow run genuinely
 * warrant an agentic AI workflow, per the Requirements Analyst agent's own
 * judgment (see backend/app/services/requirements_service.py). Used to
 * render a graceful "not qualified" banner on the Requirement Discovery Map
 * rather than silently proceeding.
 */
export function useRequirementsQualification(
  sessionId: string | null,
  workflowRunId: string | null,
  pollIntervalMs: number = DEFAULT_POLL_MS,
): AsyncResourceState<RequirementsQualification> {
  const fetcher = useCallback(async (): Promise<RequirementsQualification> => {
    if (!sessionId || !workflowRunId) {
      throw new Error("No active session or workflow run.");
    }
    return requirementsApi.getQualification(sessionId, workflowRunId);
  }, [sessionId, workflowRunId]);

  return useAsyncResource(fetcher, [sessionId, workflowRunId], {
    enabled: Boolean(sessionId && workflowRunId),
    pollIntervalMs,
  });
}
