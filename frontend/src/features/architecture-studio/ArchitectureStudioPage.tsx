import { Button, Text } from "@fluentui/react-components";
import { useSessionContext } from "@/state/SessionContext";
import {
  useArchitectureReanalysis,
  useArchitectureStudio,
  type RedesignGoal,
} from "@/hooks/useArchitectureStudio";
import { useDecisionGraph } from "@/hooks/useDecisionGraph";
import { PageHeader } from "@/layouts/AppShell";
import { LoadingState } from "@/components/LoadingState";
import { ErrorState } from "@/components/ErrorState";
import { SectionCard } from "@/components/SectionCard";

const REDESIGN_GOALS: Array<{ id: RedesignGoal; label: string }> = [
  { id: "lower_cost", label: "Lower Cost" },
  { id: "higher_security", label: "Higher Security" },
  { id: "faster_mvp", label: "Faster MVP" },
  { id: "regulated_industry", label: "Regulated Industry" },
  { id: "fabric_first", label: "Fabric-First" },
];

const POLL_MS = Number(import.meta.env.VITE_ARCHITECTURE_STUDIO_POLL_MS ?? 0);

export function ArchitectureStudioPage(): JSX.Element {
  const { sessionId, workflowRunId } = useSessionContext();
  const { data: snapshot, loading, error, refresh } = useArchitectureStudio(
    sessionId,
    workflowRunId,
    POLL_MS,
  );
  const { requestAlternative, requesting, error: reanalysisError } = useArchitectureReanalysis(
    sessionId,
    workflowRunId,
  );
  const inspector = useDecisionGraph(snapshot?.decision_graph ?? null);

  if (!workflowRunId) {
    return (
      <div>
        <PageHeader title="Architecture Studio" />
        <Text size={300} style={{ opacity: 0.7 }}>
          Start a workflow run from Mission Control to see architecture recommendations.
        </Text>
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Architecture Studio"
        subtitle="Recommended architecture components and reanalysis actions for this workflow run."
      />
      {loading && !snapshot ? <LoadingState label="Loading architecture..." /> : null}
      {error ? <ErrorState error={error} onRetry={refresh} /> : null}
      {reanalysisError ? <ErrorState error={reanalysisError} /> : null}

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 20 }}>
        {REDESIGN_GOALS.map((goal) => (
          <Button
            key={goal.id}
            size="small"
            disabled={requesting}
            onClick={() => void requestAlternative(goal.id).then(refresh)}
          >
            {goal.label}
          </Button>
        ))}
      </div>

      {snapshot ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {snapshot.components.length === 0 ? (
            <Text size={300} style={{ opacity: 0.7 }}>
              No architecture components recommended yet.
            </Text>
          ) : (
            snapshot.components.map((component) => (
              <SectionCard
                key={component.step_id}
                title={component.step_id}
                action={<Text size={200}>{component.recommended_by}</Text>}
              >
                <Text
                  size={300}
                  style={{
                    whiteSpace: "pre-wrap",
                    cursor: "pointer",
                  }}
                  onClick={() => inspector.selectNode(component.step_id)}
                >
                  {component.content}
                </Text>
              </SectionCard>
            ))
          )}
        </div>
      ) : null}

      {inspector.selectedNode ? (
        <SectionCard title="Component Inspector">
          <Text weight="semibold" style={{ display: "block" }}>
            {inspector.selectedNode.label}
          </Text>
          <pre style={{ fontSize: 11, whiteSpace: "pre-wrap" }}>
            {JSON.stringify(inspector.selectedNode.metadata, null, 2)}
          </pre>
        </SectionCard>
      ) : null}
    </div>
  );
}
