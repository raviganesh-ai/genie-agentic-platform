import { useCallback, useEffect, useMemo, useState } from "react";
import { Button, Checkbox, MessageBar, MessageBarBody, MessageBarTitle, Text } from "@fluentui/react-components";
import { useNavigate } from "react-router-dom";
import { useSessionContext } from "@/state/SessionContext";
import { useWorkshop } from "@/hooks/useWorkshop";
import { useAsyncResource } from "@/hooks/useAsyncResource";
import { workflowApi } from "@/services/workflowApi";
import { approvalApi } from "@/services/approvalApi";
import { getTraceId } from "@/state/traceRegistry";
import { ApiError } from "@/services/httpClient";
import { PageHeader } from "@/layouts/AppShell";
import { ErrorState } from "@/components/ErrorState";
import { SectionCard } from "@/components/SectionCard";
import { AgentActivityAnimation } from "@/components/AgentActivityAnimation";
import { useWorkflowEventStream, workflowStepDeltaKey } from "@/hooks/useWorkflowEventStream";
import { isBuildOutputComplete } from "@/utils/textArtifacts";
import { GeneratedArtifacts } from "./GeneratedArtifacts";

/** Gates entry into the automated peer review phase (security-assessment,
 * test-generation, peer-review - see config/workflows/registry.yaml)
 * until the user has reviewed the Build Agent's generated code here and
 * explicitly clicked "Proceed to Deploy & Launch" - mirrors the
 * architecture-approval gate on ArchitectureStudioPage. The Peer Review
 * page itself is no longer part of the guided flow - those steps still
 * run server-side and their evidence still feeds Final Output Approval on
 * Deploy & Launch. */
const PEER_REVIEW_CHECKPOINT_SUBJECT_ID = "security-assessment";

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
  const [reviewed, setReviewed] = useState(false);
  const [approving, setApproving] = useState(false);
  const [approveError, setApproveError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);

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
  const approvalsFetcher = useCallback(
    () => (sessionId ? approvalApi.list(sessionId) : Promise.reject(new Error("No session"))),
    [sessionId],
  );
  const { data: approvals, refresh: refreshApprovals } = useAsyncResource(approvalsFetcher, [sessionId], {
    enabled: Boolean(sessionId),
    pollIntervalMs: Number(import.meta.env.VITE_ARCHITECTURE_STUDIO_POLL_MS ?? 0),
  });
  const { events: liveEvents, stepDeltaText } = useWorkflowEventStream(sessionId);
  const lastLiveEvent = liveEvents[liveEvents.length - 1] ?? null;
  useEffect(() => {
    if (lastLiveEvent?.event_type === "step_completed" || lastLiveEvent?.event_type === "step_failed") {
      void refreshRun();
      void refreshApprovals();
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
  const pendingPeerReviewApproval = approvals?.find(
    (request) => request.status === "pending" && request.subject_id === PEER_REVIEW_CHECKPOINT_SUBJECT_ID,
  );
  const peerReviewAlreadyStarted = Boolean(
    run?.step_results.some((result) => result.step_id === PEER_REVIEW_CHECKPOINT_SUBJECT_ID),
  );

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

  // The approval checkpoint is created server-side once build-solution
  // finishes; approvals are polled the same as `run` above, so
  // `pendingPeerReviewApproval` is real, already-confirmed state by the
  // time this runs - no retry/guessing needed here.
  const handleProceedToDeployLaunch = useCallback(async () => {
    if (!sessionId || !workflowRunId || !pendingPeerReviewApproval) return;
    setApproving(true);
    setApproveError(null);
    try {
      await approvalApi.decide(sessionId, pendingPeerReviewApproval.id, "approved");
      const traceId = getTraceId(workflowRunId) ?? undefined;
      // Navigate straight to Deploy & Launch - the human's own review here
      // (the checkbox above) is the only gate the user needs to see; the
      // security-assessment/test-generation/peer-review steps still run
      // server-side (this resume call is what reaches peer-review's wave),
      // their evidence still feeds Final Output Approval on Deploy & Launch,
      // so nothing about that governance is skipped - only the separate
      // Peer Review page is no longer part of the guided flow. Must
      // re-supply the policies the user selected back on Architecture
      // Studio - carried forward via SessionContext since that step never
      // executes in the same call/page that originally captured them.
      navigate("/outputs");
      workflowApi
        .resumeRun(sessionId, workflowRunId, traceId, {
          "peer-review": {
            step_id: "peer-review",
            variables: { policies: governancePolicies },
          },
        })
        .catch((err) => {
          console.error("Failed to resume the workflow after the governance checkpoint approval.", err);
        });
    } catch (err) {
      setApproveError((err as ApiError).message ?? "Failed to resume the workflow.");
    } finally {
      setApproving(false);
    }
  }, [sessionId, workflowRunId, pendingPeerReviewApproval, navigate, governancePolicies]);

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
      />
      {workshop.error ? <ErrorState error={workshop.error} /> : null}

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

      {buildGenerationComplete && !peerReviewAlreadyStarted ? (
        <SectionCard title="✅ Ready to proceed?">
          {approveError ? (
            <MessageBar intent="error" style={{ marginBottom: 8 }}>
              <MessageBarBody>
                <MessageBarTitle>Failed to resume the workflow</MessageBarTitle>
                {approveError}
              </MessageBarBody>
            </MessageBar>
          ) : null}
          <Checkbox
            checked={reviewed}
            onChange={(_, data) => setReviewed(Boolean(data.checked))}
            label="AI can perform mistake, the user has reviewed and is willing to proceed"
          />
          {reviewed ? (
            // Disabled only for the brief moment before the backend's own
            // approval checkpoint is confirmed present - once enabled, this
            // click always succeeds (no retry inside the handler).
            <Button
              appearance="primary"
              style={{ marginTop: 8 }}
              disabled={approving || !pendingPeerReviewApproval}
              onClick={() => void handleProceedToDeployLaunch()}
            >
              {approving ? "Continuing..." : pendingPeerReviewApproval ? "Proceed to Deploy & Launch" : "Finishing up..."}
            </Button>
          ) : null}
        </SectionCard>
      ) : null}
    </div>
  );
}

