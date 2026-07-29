import { useCallback, useEffect, useMemo, useState } from "react";
import { Button, Checkbox, Text } from "@fluentui/react-components";
import { useNavigate } from "react-router-dom";
import { useSessionContext } from "@/state/SessionContext";
import { useWorkshop } from "@/hooks/useWorkshop";
import { useAsyncResource } from "@/hooks/useAsyncResource";
import { workflowApi } from "@/services/workflowApi";
import { PageHeader } from "@/layouts/AppShell";
import { ErrorState } from "@/components/ErrorState";
import { SectionCard } from "@/components/SectionCard";
import { LiveWorkflowPulse } from "@/components/LiveWorkflowPulse";
import { AgentActivityAnimation } from "@/components/AgentActivityAnimation";
import { useWorkflowEventStream } from "@/hooks/useWorkflowEventStream";
import { GeneratedArtifacts } from "./GeneratedArtifacts";

export function WorkshopPage(): JSX.Element {
  const navigate = useNavigate();
  const { sessionId, workflowRunId } = useSessionContext();
  const workshop = useWorkshop(sessionId, workflowRunId);
  const [reviewed, setReviewed] = useState(false);

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
  const { events: liveEvents, connected: liveConnected } = useWorkflowEventStream(sessionId);
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

      <LiveWorkflowPulse connected={liveConnected} events={liveEvents} />

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

      <SectionCard title="✅ Ready to proceed?">
        <Checkbox
          checked={reviewed}
          onChange={(_, data) => setReviewed(Boolean(data.checked))}
          label="AI can perform mistake, the user has reviewed and is willing to proceed"
        />
        {reviewed ? (
          <Button appearance="primary" style={{ marginTop: 8 }} onClick={() => navigate("/governance")}>
            Proceed to Governance
          </Button>
        ) : null}
      </SectionCard>
    </div>
  );
}
