import { useCallback, useEffect, useMemo, useState } from "react";
import { Button, Text, Textarea } from "@fluentui/react-components";
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
import type { WorkflowStepResult } from "@/types/workflow";

export function WorkshopPage(): JSX.Element {
  const { sessionId, workflowRunId } = useSessionContext();
  const workshop = useWorkshop(sessionId, workflowRunId);
  const [message, setMessage] = useState("");
  const [priorityRationale, setPriorityRationale] = useState("");
  const [lastStepResults, setLastStepResults] = useState<WorkflowStepResult[]>([]);

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
        subtitle="Watch Genie call the Orchestrator Agent to generate this mission's React UI code and multi-agent code, then chat, challenge, and adjust priorities in real time."
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

      <SectionCard title="Chat with all agents">
        <Textarea
          value={message}
          onChange={(_, data) => setMessage(data.value)}
          placeholder="Ask a question or provide direction..."
          style={{ width: "100%", marginBottom: 8 }}
        />
        <Button
          appearance="primary"
          disabled={workshop.busy || !message.trim()}
          onClick={() =>
            void workshop.sendMessage(message).then((result) => {
              setLastStepResults(result.step_results);
              setMessage("");
            })
          }
        >
          Send
        </Button>
      </SectionCard>

      {lastStepResults.length > 0 ? (
        <SectionCard title="Latest Agent Responses">
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {lastStepResults.map((step) => (
              <div key={step.step_id}>
                <Text weight="semibold" size={300} style={{ display: "block" }}>
                  {step.agent_id}
                </Text>
                <Text size={300} style={{ whiteSpace: "pre-wrap" }}>
                  {step.output_text ?? step.error ?? "(no output)"}
                </Text>
              </div>
            ))}
          </div>
        </SectionCard>
      ) : null}

      <SectionCard title="Adjust Priorities">
        <Textarea
          value={priorityRationale}
          onChange={(_, data) => setPriorityRationale(data.value)}
          placeholder="Describe the priority change you want applied..."
          style={{ width: "100%", marginBottom: 8 }}
        />
        <Button
          disabled={workshop.busy || !priorityRationale.trim()}
          onClick={() =>
            void workshop.updatePriorities(priorityRationale).then(() =>
              setPriorityRationale(""),
            )
          }
        >
          Submit Priority Change
        </Button>
      </SectionCard>
    </div>
  );
}
