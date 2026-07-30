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
import { useWorkflowEventStream } from "@/hooks/useWorkflowEventStream";
import { GeneratedArtifacts } from "./GeneratedArtifacts";

/** Gates entry into the automated governance phase (security-assessment,
 * test-generation, governance-review - see config/workflows/registry.yaml)
 * until the user has reviewed the Build Agent's generated code here and
 * explicitly clicked "Proceed to Governance" - mirrors the
 * architecture-approval gate on ArchitectureStudioPage. */
const GOVERNANCE_CHECKPOINT_SUBJECT_ID = "security-assessment";

export function WorkshopPage(): JSX.Element {
  const navigate = useNavigate();
  const { sessionId, workflowRunId } = useSessionContext();
  const workshop = useWorkshop(sessionId, workflowRunId);
  const [reviewed, setReviewed] = useState(false);
  const [approving, setApproving] = useState(false);
  const [approveError, setApproveError] = useState<string | null>(null);

  // The Build Agent's UI + multi-agent workflow design is the
  // build-solution step's own output (same run the Architecture/Governance
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
  const { events: liveEvents } = useWorkflowEventStream(sessionId);
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
  const pendingGovernanceApproval = approvals?.find(
    (request) => request.status === "pending" && request.subject_id === GOVERNANCE_CHECKPOINT_SUBJECT_ID,
  );
  const governanceAlreadyStarted = Boolean(
    run?.step_results.some((result) => result.step_id === GOVERNANCE_CHECKPOINT_SUBJECT_ID),
  );

  const handleProceedToGovernance = useCallback(async () => {
    if (!sessionId || !workflowRunId || !pendingGovernanceApproval) return;
    setApproving(true);
    setApproveError(null);
    try {
      await approvalApi.decide(sessionId, pendingGovernanceApproval.id, "approved");
      const traceId = getTraceId(workflowRunId) ?? undefined;
      // Navigate immediately - the Governance page has its own live event
      // stream + polling and shows progress until governance-review's
      // output arrives, the same pattern ArchitectureStudioPage uses for
      // the architecture-approval -> build-solution handoff.
      navigate("/governance");
      workflowApi.resumeRun(sessionId, workflowRunId, traceId).catch((err) => {
        console.error("Failed to resume the workflow after the governance checkpoint approval.", err);
      });
    } catch (err) {
      setApproveError((err as ApiError).message ?? "Failed to resume the workflow.");
    } finally {
      setApproving(false);
    }
  }, [sessionId, workflowRunId, pendingGovernanceApproval, navigate]);

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
        {buildOutputText ? (
          <GeneratedArtifacts
            outputText={buildOutputText}
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

      {buildOutputText && !governanceAlreadyStarted ? (
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
              disabled={!pendingGovernanceApproval || approving}
              onClick={() => void handleProceedToGovernance()}
            >
              {approving
                ? "Continuing..."
                : pendingGovernanceApproval
                  ? "Proceed to Governance"
                  : "Preparing governance checkpoint..."}
            </Button>
          ) : null}
        </SectionCard>
      ) : null}
    </div>
  );
}

