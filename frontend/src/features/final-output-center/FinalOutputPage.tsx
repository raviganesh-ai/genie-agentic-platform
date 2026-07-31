import { useCallback, useEffect, useMemo, useState } from "react";
import { Button, Text } from "@fluentui/react-components";
import { useNavigate } from "react-router-dom";
import { useSessionContext } from "@/state/SessionContext";
import { useFinalOutput, useFinalOutputTypes, exportDeliverable } from "@/hooks/useFinalOutput";
import { useAsyncResource } from "@/hooks/useAsyncResource";
import { workflowApi } from "@/services/workflowApi";
import { PageHeader } from "@/layouts/AppShell";
import { LoadingState } from "@/components/LoadingState";
import { ErrorState } from "@/components/ErrorState";
import { SectionCard } from "@/components/SectionCard";
import { LiveWorkflowPulse } from "@/components/LiveWorkflowPulse";
import { AgentActivityAnimation } from "@/components/AgentActivityAnimation";
import { useWorkflowEventStream } from "@/hooks/useWorkflowEventStream";
import type { DeliverablePackage, DeliverableType } from "@/types/workflow";

const DELIVERABLE_LABELS: Record<DeliverableType, string> = {
  requirements_package: "Requirements Package",
  architecture_package: "Architecture Package",
  roadmap_package: "Roadmap Package",
  executive_summary_package: "Executive Summary",
  final_output_package: "Final Output Package",
};

const DELIVERABLE_ICONS: Record<DeliverableType, string> = {
  requirements_package: "📋",
  architecture_package: "🏗️",
  roadmap_package: "🗺️",
  executive_summary_package: "📊",
  final_output_package: "📦",
};

function iconForSectionTitle(title: string): string {
  const normalized = title.toLowerCase();
  if (/risk/.test(normalized)) return "⚠️";
  if (/cost|budget|pricing/.test(normalized)) return "💰";
  if (/timeline|roadmap|phase/.test(normalized)) return "🗓️";
  if (/security|compliance|governance/.test(normalized)) return "🔒";
  if (/architecture|component|design/.test(normalized)) return "🏗️";
  if (/summary|overview/.test(normalized)) return "📊";
  if (/requirement|goal|constraint/.test(normalized)) return "📋";
  return "📄";
}

export function FinalOutputPage(): JSX.Element {
  const navigate = useNavigate();
  const { sessionId, workflowRunId } = useSessionContext();
  const { data: types, loading, error, refresh } = useFinalOutputTypes(sessionId);
  const { generate, generating, error: generateError } = useFinalOutput(sessionId);
  const [generated, setGenerated] = useState<DeliverablePackage | null>(null);

  const runFetcher = useCallback(
    () =>
      sessionId && workflowRunId
        ? workflowApi.getRun(sessionId, workflowRunId)
        : Promise.reject(new Error("No active workflow run")),
    [sessionId, workflowRunId],
  );
  const { data: run, refresh: refreshRun } = useAsyncResource(runFetcher, [sessionId, workflowRunId], {
    enabled: Boolean(sessionId && workflowRunId),
    pollIntervalMs: 5000,
  });
  const { events: liveEvents, connected: liveConnected } = useWorkflowEventStream(sessionId);
  const lastLiveEvent = liveEvents[liveEvents.length - 1] ?? null;
  useEffect(() => {
    if (lastLiveEvent?.event_type === "step_completed" || lastLiveEvent?.event_type === "step_failed") {
      void refreshRun();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastLiveEvent]);
  const testSuiteOutput = useMemo(
    () => run?.step_results.find((result) => result.step_id === "test-generation")?.output_text ?? "",
    [run],
  );
  const reviewComplete = Boolean(
    run?.step_results.some((result) => result.step_id === "peer-review"),
  );

  if (!workflowRunId) {
    return (
      <div>
        <PageHeader title="Final Output Center" />
        <Text size={300} style={{ opacity: 0.7 }}>
          Start a workflow run from Upload to generate final deliverables.
        </Text>
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Final Output Center"
        subtitle="Generate and export the final deliverable packages for this session."
      />
      {loading && !types ? <LoadingState label="Loading deliverable types..." /> : null}
      {error ? <ErrorState error={error} onRetry={refresh} /> : null}
      {generateError ? <ErrorState error={generateError} /> : null}

      <LiveWorkflowPulse connected={liveConnected} events={liveEvents} />

      {!reviewComplete ? (
        <AgentActivityAnimation
          label="Genie is working with the Peer Review agent to finish reviewing your solution..."
          events={liveEvents}
        />
      ) : null}

      {testSuiteOutput ? (
        <SectionCard title="🧪 Generated Test Suite">
          <Text size={300} style={{ whiteSpace: "pre-wrap" }}>
            {testSuiteOutput}
          </Text>
        </SectionCard>
      ) : null}

      {reviewComplete ? (
        <SectionCard
          title="🚀 Deploy & Launch"
          action={
            <Button appearance="primary" onClick={() => navigate("/outputs")}>
              Go to Deploy & Launch
            </Button>
          }
        >
          <Text size={300} style={{ opacity: 0.8 }}>
            Peer Review has finished. Head to Deploy & Launch to provision access control, deploy
            the agents and app, run full testing and a security scan, and get the customer-facing
            launch link.
          </Text>
        </SectionCard>
      ) : null}

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 20 }}>
        {types?.map((type) => (
          <Button
            key={type}
            appearance="primary"
            disabled={generating}
            onClick={() => void generate(workflowRunId, type).then(setGenerated)}
          >
            {DELIVERABLE_ICONS[type] ?? "📄"} Generate {DELIVERABLE_LABELS[type] ?? type}
          </Button>
        ))}
      </div>

      {generated ? (
        <SectionCard
          title={`${DELIVERABLE_ICONS[generated.deliverable_type] ?? "📄"} ${DELIVERABLE_LABELS[generated.deliverable_type] ?? generated.deliverable_type}`}
          action={
            <div style={{ display: "flex", gap: 8 }}>
              <Button size="small" onClick={() => exportDeliverable(generated, "json")}>
                Export JSON
              </Button>
              <Button size="small" onClick={() => exportDeliverable(generated, "markdown")}>
                Export Markdown
              </Button>
            </div>
          }
        >
          {Object.entries(generated.sections).map(([title, body]) => (
            <div key={title} style={{ marginBottom: 12 }}>
              <Text weight="semibold" style={{ display: "block" }}>
                {iconForSectionTitle(title)} {title}
              </Text>
              <Text size={300} style={{ whiteSpace: "pre-wrap" }}>
                {body}
              </Text>
            </div>
          ))}
        </SectionCard>
      ) : null}
    </div>
  );
}
