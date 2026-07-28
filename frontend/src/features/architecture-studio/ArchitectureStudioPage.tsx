import { useCallback, useMemo, useState } from "react";
import {
  Button,
  Checkbox,
  MessageBar,
  MessageBarBody,
  MessageBarTitle,
  Text,
  Textarea,
} from "@fluentui/react-components";
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
import { ArchitectureComponentDiagram } from "./ArchitectureComponentDiagram";
import type { ApiError } from "@/services/httpClient";

const REDESIGN_GOALS: Array<{ id: RedesignGoal; label: string; icon: string }> = [
  { id: "lower_cost", label: "Lower Cost", icon: "💰" },
  { id: "higher_security", label: "Higher Security", icon: "🔒" },
  { id: "faster_mvp", label: "Faster MVP", icon: "⚡" },
  { id: "regulated_industry", label: "Regulated Industry", icon: "⚖️" },
  { id: "fabric_first", label: "Fabric-First", icon: "🧵" },
];

const GRAPH_LEGEND: Array<{ label: string; color: string }> = [
  { label: "Agent", color: "#2f83e0" },
  { label: "Recommendation", color: "#5aa16c" },
  { label: "Approval", color: "#c98a2c" },
  { label: "Memory record", color: "#8a63d2" },
  { label: "Evidence", color: "#5c6572" },
];

const GOVERNANCE_POLICY_OPTIONS: string[] = [
  "Must use managed identity (no embedded credentials)",
  "No hardcoded secrets, keys, or connection strings",
  "Least-privilege data access",
  "Data encrypted at rest and in transit",
  "Audit logging / governance trace enabled for all actions",
  "Network isolation (private endpoints / no public data access)",
  "Detailed error handling (no silent failures, clear error messages)",
];

const OTHER_POLICY_OPTION = "Other";

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
  const [selectedPolicies, setSelectedPolicies] = useState<Record<string, boolean>>({});
  const [otherPolicyChecked, setOtherPolicyChecked] = useState(false);
  const [otherPolicyText, setOtherPolicyText] = useState("");
  const [approving, setApproving] = useState(false);
  const [approveError, setApproveError] = useState<string | null>(null);

  const togglePolicy = useCallback((option: string, checked: boolean) => {
    setSelectedPolicies((prev) => ({ ...prev, [option]: checked }));
  }, []);

  const effectiveGovernancePolicies = useMemo(() => {
    const parts = GOVERNANCE_POLICY_OPTIONS.filter((option) => selectedPolicies[option]);
    if (otherPolicyChecked && otherPolicyText.trim().length > 0) {
      parts.push(otherPolicyText.trim());
    }
    return parts.join("; ");
  }, [selectedPolicies, otherPolicyChecked, otherPolicyText]);

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
          variables: { policies: effectiveGovernancePolicies },
        },
      });
      await Promise.all([refresh(), refreshApprovals()]);
    } catch (err) {
      setApproveError((err as ApiError).message ?? "Failed to resume the workflow.");
    } finally {
      setApproving(false);
    }
  }, [
    sessionId,
    workflowRunId,
    pendingArchitectureApproval,
    effectiveGovernancePolicies,
    refresh,
    refreshApprovals,
  ]);

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

      <Text size={200} weight="semibold" style={{ display: "block", marginBottom: 8, opacity: 0.75 }}>
        Request an alternative design
      </Text>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 20 }}>
        {REDESIGN_GOALS.map((goal) => (
          <Button
            key={goal.id}
            size="small"
            disabled={requesting}
            onClick={() => void requestAlternative(goal.id).then(refresh)}
          >
            {goal.icon} {goal.label}
          </Button>
        ))}
      </div>

      {snapshot ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <SectionCard title="🗺️ Mission Control Flow (Visual)">
            <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginBottom: 10 }}>
              {GRAPH_LEGEND.map((entry) => (
                <div key={entry.label} style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span
                    style={{
                      display: "inline-block",
                      width: 10,
                      height: 10,
                      borderRadius: "50%",
                      backgroundColor: entry.color,
                    }}
                  />
                  <Text size={100} style={{ opacity: 0.7 }}>
                    {entry.label}
                  </Text>
                </div>
              ))}
            </div>
            <ArchitectureFlowGraph
              graph={snapshot.decision_graph}
              onNodeClick={inspector.selectNode}
              onEdgeClick={inspector.selectEdge}
            />
            <Text size={100} style={{ opacity: 0.55, display: "block", marginTop: 8 }}>
              Click any node to inspect its details below.
            </Text>
          </SectionCard>
          {snapshot.components.length === 0 ? (
            <Text size={300} style={{ opacity: 0.7 }}>
              No architecture components recommended yet.
            </Text>
          ) : (
            snapshot.components.map((component) => (
              <SectionCard
                key={component.step_id}
                title={`🏗️ ${component.step_id.replace(/-/g, " ")}`}
                action={<Text size={200}>{component.recommended_by}</Text>}
              >
                <ArchitectureComponentDiagram content={component.content} />
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
            review. Select the policies the generated code should be evaluated against:
          </Text>
          <div style={{ display: "flex", flexDirection: "column", gap: 4, marginBottom: 8 }}>
            {GOVERNANCE_POLICY_OPTIONS.map((option) => (
              <Checkbox
                key={option}
                label={option}
                checked={Boolean(selectedPolicies[option])}
                onChange={(_, data) => togglePolicy(option, Boolean(data.checked))}
              />
            ))}
            <Checkbox
              label={OTHER_POLICY_OPTION}
              checked={otherPolicyChecked}
              onChange={(_, data) => setOtherPolicyChecked(Boolean(data.checked))}
            />
          </div>
          {otherPolicyChecked ? (
            <Textarea
              value={otherPolicyText}
              onChange={(_, dataEv) => setOtherPolicyText(dataEv.value)}
              rows={3}
              placeholder="Describe the additional policy/policies to evaluate against..."
              style={{ width: "100%", marginBottom: 12 }}
            />
          ) : null}
          <Button
            appearance="primary"
            disabled={approving || effectiveGovernancePolicies.trim().length === 0}
            onClick={() => void handleApproveArchitecture()}
          >
            {approving ? "Continuing..." : "Approve Architecture & Generate Code"}
          </Button>
        </SectionCard>
      ) : null}

      {inspector.selectedNode ? (
        <SectionCard title="🔍 Node Inspector" action={<Button size="small" onClick={inspector.clearSelection}>Close</Button>}>
          <Text weight="semibold" style={{ display: "block" }}>
            {inspector.selectedNode.label}
          </Text>
          <pre style={{ fontSize: 11, whiteSpace: "pre-wrap" }}>
            {JSON.stringify(inspector.selectedNode.metadata, null, 2)}
          </pre>
        </SectionCard>
      ) : null}
      {inspector.selectedEdge ? (
        <SectionCard title="🔍 Edge Inspector" action={<Button size="small" onClick={inspector.clearSelection}>Close</Button>}>
          <Text weight="semibold" style={{ display: "block" }}>
            {inspector.selectedEdge.edge_type.replace(/_/g, " ")}
          </Text>
          <pre style={{ fontSize: 11, whiteSpace: "pre-wrap" }}>
            {JSON.stringify(inspector.selectedEdge.metadata, null, 2)}
          </pre>
        </SectionCard>
      ) : null}
    </div>
  );
}
