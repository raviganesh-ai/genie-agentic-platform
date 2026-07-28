import { useCallback, useState } from "react";
import { Button, MessageBar, MessageBarBody, MessageBarTitle, Text, Textarea } from "@fluentui/react-components";
import { useSessionContext } from "@/state/SessionContext";
import {
  useArchitectureReanalysis,
  useArchitectureStudio,
  type RedesignGoal,
} from "@/hooks/useArchitectureStudio";
import { useDecisionGraph } from "@/hooks/useDecisionGraph";
import { useAsyncResource } from "@/hooks/useAsyncResource";
import { approvalApi } from "@/services/approvalApi";
import { workflowApi } from "@/services/workflowApi";
import { getTraceId } from "@/state/traceRegistry";
import { PageHeader } from "@/layouts/AppShell";
import { LoadingState } from "@/components/LoadingState";
import { ErrorState } from "@/components/ErrorState";
import { SectionCard } from "@/components/SectionCard";
import { ArchitectureFlowGraph } from "./ArchitectureFlowGraph";
import type { ApiError } from "@/services/httpClient";

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

  const approvalsFetcher = useCallback(
    () => (sessionId ? approvalApi.list(sessionId) : Promise.reject(new Error("No session"))),
    [sessionId],
  );
  const { data: approvals, refresh: refreshApprovals } = useAsyncResource(
    approvalsFetcher,
    [sessionId],
    { enabled: Boolean(sessionId) },
  );
  const pendingArchitectureApproval = approvals?.find(
    (request) => request.status === "pending" && request.subject_id === "build-solution",
  );
  const [governancePolicies, setGovernancePolicies] = useState("");
  const [approving, setApproving] = useState(false);
  const [approveError, setApproveError] = useState<string | null>(null);

  const handleApproveArchitecture = useCallback(async () => {
    if (!sessionId || !workflowRunId || !pendingArchitectureApproval) return;
    setApproving(true);
    setApproveError(null);
    try {
      await approvalApi.decide(sessionId, pendingArchitectureApproval.id, "approved");
      await refreshApprovals();
      const traceId = getTraceId(workflowRunId) ?? undefined;
      await workflowApi.resumeRun(sessionId, workflowRunId, traceId, {
        "governance-review": {
          step_id: "governance-review",
          variables: { policies: governancePolicies },
        },
      });
      await Promise.all([refresh(), refreshApprovals()]);
    } catch (err) {
      setApproveError((err as ApiError).message ?? "Failed to resume the workflow.");
    } finally {
      setApproving(false);
    }
  }, [sessionId, workflowRunId, pendingArchitectureApproval, governancePolicies, refresh, refreshApprovals]);

  if (!workflowRunId) {
    return (
      <div>
        <PageHeader title="Architecture Studio" />
        <Text size={300} style={{ opacity: 0.7 }}>
          Start a workflow run from Upload to see architecture recommendations.
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
          <SectionCard title="UI & Agent Flow (Visual)">
            <ArchitectureFlowGraph graph={snapshot.decision_graph} />
          </SectionCard>
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

      {pendingArchitectureApproval ? (
        <SectionCard title="Approve Architecture">
          {approveError ? (
            <MessageBar intent="error" layout="multiline" style={{ marginBottom: 12 }}>
              <MessageBarBody>
                <MessageBarTitle>Failed to continue the mission</MessageBarTitle>
                {approveError}
              </MessageBarBody>
            </MessageBar>
          ) : null}
          <Text size={300} style={{ display: "block", marginBottom: 8, opacity: 0.8 }}>
            Approving generates the UI + agent workflow code and runs the governance/security
            review. Provide the policies the generated code should be evaluated against:
          </Text>
          <Textarea
            value={governancePolicies}
            onChange={(_, dataEv) => setGovernancePolicies(dataEv.value)}
            rows={4}
            placeholder="e.g. Must use managed identity, no hardcoded secrets, least-privilege data access..."
            style={{ width: "100%", marginBottom: 12 }}
          />
          <Button
            appearance="primary"
            disabled={approving || governancePolicies.trim().length === 0}
            onClick={() => void handleApproveArchitecture()}
          >
            {approving ? "Continuing..." : "Approve Architecture & Generate Code"}
          </Button>
        </SectionCard>
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
