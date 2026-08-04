import { useCallback, useEffect, useMemo, useState } from "react";
import { Button, Checkbox, Text } from "@fluentui/react-components";
import { useNavigate } from "react-router-dom";
import { useSessionContext } from "@/state/SessionContext";
import { useWorkshop } from "@/hooks/useWorkshop";
import { useAsyncResource } from "@/hooks/useAsyncResource";
import { workflowApi } from "@/services/workflowApi";
import { getTraceId } from "@/state/traceRegistry";
import { ApiError } from "@/services/httpClient";
import { PageHeader } from "@/layouts/AppShell";
import { ErrorState } from "@/components/ErrorState";
import { SectionCard } from "@/components/SectionCard";
import { AgentActivityAnimation } from "@/components/AgentActivityAnimation";
import { useWorkflowEventStream, workflowStepDeltaKey } from "@/hooks/useWorkflowEventStream";
import { isBuildOutputComplete } from "@/utils/textArtifacts";
import { GeneratedArtifacts } from "./GeneratedArtifacts";

/** The build-solution step always delegates to this specialist (see the
 * `call_build_agent` entry in `_DELEGATIONS`, backend/app/agents/tools/
 * orchestration_tools.py) - its own real streamed output (not
 * genie-orchestrator's later echo of the same text) is what should be
 * rendered live while the Build Agent is still generating. */
const BUILD_STEP_ID = "build-solution";
const BUILD_AGENT_ID = "build-agent";

export function WorkshopPage(): JSX.Element {
  const navigate = useNavigate();
  const { sessionId, workflowRunId, governancePolicies } = useSessionContext();
  const workshop = useWorkshop(sessionId, workflowRunId);
  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);
  const [rerunningBuild, setRerunningBuild] = useState(false);
  const [rerunBuildError, setRerunBuildError] = useState<string | null>(null);
  const [reviewAcknowledged, setReviewAcknowledged] = useState(false);

  // The Build Agent's UI + multi-agent workflow design is the
  // build-solution step's own output (same run the Architecture/Peer Review
  // pages read from) - shown as one-by-one generated artifact cards below
  // instead of buried in chat.
  const runFetcher = useCallback(
    () =>
      sessionId && workflowRunId
        ? workflowApi.getRun(sessionId, workflowRunId)
        : Promise.reject(new Error("No active workflow run")),
    [sessionId, workflowRunId],
  );
  const { data: run, refresh: refreshRun } = useAsyncResource(runFetcher, [sessionId, workflowRunId], {
    enabled: Boolean(sessionId && workflowRunId),
    pollIntervalMs: Number(import.meta.env.VITE_ARCHITECTURE_STUDIO_POLL_MS ?? 0),
  });
  const { events: liveEvents, stepDeltaText } = useWorkflowEventStream(sessionId);
  const lastLiveEvent = liveEvents[liveEvents.length - 1] ?? null;
  useEffect(() => {
    if (lastLiveEvent?.event_type === "step_completed" || lastLiveEvent?.event_type === "step_failed") {
      void refreshRun();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastLiveEvent]);
  const buildStepResult = useMemo(
    () => run?.step_results.find((result) => result.step_id === "build-solution"),
    [run],
  );
  const buildOutputText = buildStepResult?.output_text ?? "";
  const buildError = buildStepResult?.error ?? null;
  // The Build Agent's own real streamed content so far, tagged with its own
  // agent_id (never genie-orchestrator's later verbatim echo of the same
  // text - see _stream_and_publish_deltas in orchestration_tools.py) - shown
  // progressively while build-solution is still running, before `run`'s
  // next poll confirms `buildOutputText` above is the final, authoritative
  // text.
  const liveBuildText = stepDeltaText[workflowStepDeltaKey(BUILD_STEP_ID, BUILD_AGENT_ID)] ?? "";
  const displayedBuildText = buildOutputText || liveBuildText;
  // True as soon as the Build Agent's own real generation (every
  // specialist agent, the Orchestrator Agent, then the UI) has actually
  // finished streaming - well before `buildOutputText` above is populated,
  // since that instead waits for genie-orchestrator's own separate,
  // strictly-slower verbatim echo of the same text to complete server-side
  // (see isBuildOutputComplete's own doc comment). Gating the checkbox/
  // proceed button below on this instead means the user is not stuck
  // staring at fully-generated code with no way to proceed while that
  // redundant echo is still being generated.
  const buildGenerationComplete = Boolean(buildOutputText) || isBuildOutputComplete(displayedBuildText);

  // The build-solution step can fail (e.g. a transient Foundry/agent
  // execution error) - the backend now stores that as a retryable "failed"
  // WorkflowRunResult (see workflow_runtime.py) instead of losing all
  // progress, so simply resuming the SAME workflow_run_id re-attempts
  // exactly this still-pending step without restarting the whole mission.
  const handleRetryBuild = useCallback(async () => {
    if (!sessionId || !workflowRunId) return;
    setRetrying(true);
    setRetryError(null);
    try {
      const traceId = getTraceId(workflowRunId) ?? undefined;
      await workflowApi.resumeRun(sessionId, workflowRunId, traceId);
      await refreshRun();
    } catch (err) {
      setRetryError((err as ApiError).message ?? "Failed to retry the build step.");
    } finally {
      setRetrying(false);
    }
  }, [sessionId, workflowRunId, refreshRun]);

  const handleRerunBuildStage = useCallback(async () => {
    if (!sessionId || !workflowRunId) return;
    setRerunningBuild(true);
    setRerunBuildError(null);
    try {
      const traceId = getTraceId(workflowRunId) ?? undefined;
      await workflowApi.resumeRun(sessionId, workflowRunId, traceId, {
        "build-solution": {
          step_id: "build-solution",
          variables: governancePolicies.trim().length > 0 ? { policies: governancePolicies } : {},
        },
      });
      await refreshRun();
    } catch (err) {
      setRerunBuildError((err as ApiError).message ?? "Failed to re-run UI & Agent Design.");
    } finally {
      setRerunningBuild(false);
    }
  }, [sessionId, workflowRunId, governancePolicies, refreshRun]);

  // Once the user has ticked the risk-acknowledgment checkbox and clicks
  // Proceed, go straight to Deploy & Launch - no approval checkpoint to
  // decide and no workflow steps to resume in the background first. Deploy
  // & Launch itself is where Genie's orchestrator-driven pipeline actually
  // runs.
  const handleProceedToDeployLaunch = useCallback(() => {
    navigate("/outputs");
  }, [navigate]);

  if (!workflowRunId || !sessionId) {
    return (
      <div>
        <PageHeader title="Workshop" />
        <Text size={300} style={{ opacity: 0.7 }}>
          Start a workflow run from Upload to open the workshop.
        </Text>
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="UI & Agent Design"
        subtitle="Watch Genie call the Orchestrator Agent to generate this mission's React UI code and multi-agent code."
        action={
          <Button
            size="small"
            disabled={rerunningBuild}
            onClick={() => void handleRerunBuildStage()}
          >
            {rerunningBuild ? "Re-running stage..." : "Re-run UI & Agent Design"}
          </Button>
        }
      />
      {workshop.error ? <ErrorState error={workshop.error} /> : null}
      {rerunBuildError ? <ErrorState error={{ message: rerunBuildError }} /> : null}

      <SectionCard title="🛠️ Generated Artifacts">
        {displayedBuildText ? (
          <GeneratedArtifacts
            sessionId={sessionId}
            outputText={displayedBuildText}
            revealImmediately={!buildOutputText}
          />
        ) : buildError ? (
          <ErrorState
            error={{ message: retryError ?? buildError }}
            onRetry={retrying ? undefined : handleRetryBuild}
          />
        ) : (
          <AgentActivityAnimation
            label="Genie is calling the Orchestrator Agent to generate your extensive UI, each specialist agent's own code, and the Orchestrator Agent's orchestration code..."
            events={liveEvents}
          />
        )}
      </SectionCard>

      {buildGenerationComplete ? (
        <SectionCard title="✅ Ready to proceed?">
          <Checkbox
            label="AI can perform mistake, the user has reviewed and is willing to proceed"
            checked={reviewAcknowledged}
            onChange={(_, data) => setReviewAcknowledged(Boolean(data.checked))}
          />
          {reviewAcknowledged ? (
            <Button
              appearance="primary"
              style={{ marginTop: 8 }}
              onClick={handleProceedToDeployLaunch}
            >
              Proceed to Deploy & Launch
            </Button>
          ) : null}
        </SectionCard>
      ) : null}
    </div>
  );
}

