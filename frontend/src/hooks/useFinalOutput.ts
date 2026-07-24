import { useCallback, useState } from "react";
import { outputApi } from "@/services/outputApi";
import { ApiError } from "@/services/httpClient";
import { useAsyncResource, type AsyncResourceState } from "./useAsyncResource";
import type { DeliverablePackage, DeliverableType } from "@/types/workflow";
import type { SafeError } from "@/types/common";

const DEFAULT_POLL_MS = Number(import.meta.env.VITE_FINAL_OUTPUT_POLL_MS ?? 0);

export function useFinalOutputTypes(
  sessionId: string | null,
  pollIntervalMs: number = DEFAULT_POLL_MS,
): AsyncResourceState<DeliverableType[]> {
  const fetcher = useCallback(() => {
    if (!sessionId) return Promise.reject(new Error("No active session"));
    return outputApi.supportedDeliverableTypes(sessionId);
  }, [sessionId]);

  return useAsyncResource(fetcher, [sessionId], {
    enabled: Boolean(sessionId),
    pollIntervalMs,
  });
}

export interface FinalOutputController {
  generate: (
    workflowRunId: string,
    deliverableType: DeliverableType,
  ) => Promise<DeliverablePackage>;
  generating: boolean;
  error: SafeError | null;
}

export function useFinalOutput(sessionId: string | null): FinalOutputController {
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<SafeError | null>(null);

  const generate = useCallback(
    async (workflowRunId: string, deliverableType: DeliverableType) => {
      if (!sessionId) throw new Error("No active session");
      setGenerating(true);
      setError(null);
      try {
        return await outputApi.generateDeliverable(sessionId, workflowRunId, deliverableType);
      } catch (err) {
        setError(
          err instanceof ApiError ? err : { message: "Unable to generate this deliverable." },
        );
        throw err;
      } finally {
        setGenerating(false);
      }
    },
    [sessionId],
  );

  return { generate, generating, error };
}

/** Exports a generated deliverable package as downloadable JSON or Markdown. */
export function exportDeliverable(pkg: DeliverablePackage, format: "json" | "markdown"): void {
  const filename = `${pkg.deliverable_type}-${pkg.workflow_run_id}.${format === "json" ? "json" : "md"}`;
  const content =
    format === "json"
      ? JSON.stringify(pkg, null, 2)
      : Object.entries(pkg.sections)
          .map(([title, body]) => `## ${title}\n\n${body}`)
          .join("\n\n");
  const blob = new Blob([content], {
    type: format === "json" ? "application/json" : "text/markdown",
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
