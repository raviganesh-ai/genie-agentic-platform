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
import { GeneratedArtifacts } from "./GeneratedArtifacts";

/** Gates entry into the automated peer review phase (security-assessment,
 * test-generation, peer-review - see config/workflows/registry.yaml)
 * until the user has reviewed the Build Agent's generated code here and
 * explicitly clicked "Proceed to Peer Review" - mirrors the
 * architecture-approval gate on ArchitectureStudioPage. */
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
  const pendingPeerReviewApproval = approvals?.find(
    (request) => request.status === "pending" && request.subject_id === PEER_REVIEW_CHECKPOINT_SUBJECT_ID,
  );
  const peerReviewAlreadyStarted = Boolean(
    run?.step_results.some((result) => result.step_id === PEER_REVIEW_CHECKPOINT_SUBJECT_ID),
  );

  const handleProceedToPeerReview = useCallback(async () => {
    if (!sessionId || !workflowRunId || !pendingPeerReviewApproval) return;
    setApproving(true);
    setApproveError(null);
    try {
      await approvalApi.decide(sessionId, pendingPeerReviewApproval.id, "approved");
      const traceId = getTraceId(workflowRunId) ?? undefined;
      // Navigate immediately - the Peer Review page has its own live event
      // stream + polling and shows progress until peer-review's output
      // arrives, the same pattern ArchitectureStudioPage uses for the
      // architecture-approval -> build-solution handoff. This resume call
      // is the one that actually reaches peer-review's wave
      // (security-assessment/test-generation execute first in the same
      // call, then peer-review immediately after), so it must re-supply
      // the policies the user selected back on Architecture Studio -
      // carried forward via SessionContext since that step never executes
      // in the same call/page that originally captured them.
      navigate("/peer-review");
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

  if (!workflowRunId) {
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
            outputText={displayedBuildText}
            revealImmediately={!buildOutputText}
            onRegenerateArtifact={(artifactTitle, instruction) =>
              workshop
                .regenerateBuild(
                  `Regenerate only the "${artifactTitle}" artifact based on this instruction, keeping every other artifact (the UI code, every other agent's code, and the Multi-Agent Workflow section) exactly the same as before: ${instruction}`,
                )
                .then(() => {
                  void refreshRun();
                })
            }
          />
        ) : buildError ? (
          <ErrorState error={{ message: buildError }} />
        ) : (
          <AgentActivityAnimation
            label="Genie is calling the Orchestrator Agent to generate your extensive UI, each specialist agent's own code, and the Orchestrator Agent's orchestration code..."
            events={liveEvents}
          />
        )}
      </SectionCard>

      {buildOutputText && !peerReviewAlreadyStarted ? (
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
            <Button
              appearance="primary"
              style={{ marginTop: 8 }}
              disabled={!pendingPeerReviewApproval || approving}
              onClick={() => void handleProceedToPeerReview()}
            >
              {approving
                ? "Continuing..."
                : pendingPeerReviewApproval
                  ? "Proceed to Peer Review"
                  : "Preparing peer review checkpoint..."}
            </Button>
          ) : null}
        </SectionCard>
      ) : null}
    </div>
  );
}

