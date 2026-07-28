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
import { useAgentRegistry } from "@/hooks/useAgentRegistry";
import { useAsyncResource } from "@/hooks/useAsyncResource";
import { approvalApi } from "@/services/approvalApi";
import { workflowApi } from "@/services/workflowApi";
import { getTraceId } from "@/state/traceRegistry";
import { PageHeader } from "@/layouts/AppShell";
import { LoadingState } from "@/components/LoadingState";
import { ErrorState } from "@/components/ErrorState";
import { SectionCard } from "@/components/SectionCard";
import { InteractiveFlowDiagram, type FlowDiagramNode } from "./InteractiveFlowDiagram";
import { ArchitectureComponentDiagram } from "./ArchitectureComponentDiagram";
import type { ApiError } from "@/services/httpClient";

const REDESIGN_GOALS: Array<{ id: RedesignGoal; label: string; icon: string }> = [
  { id: "lower_cost", label: "Lower Cost", icon: "💰" },
  { id: "higher_security", label: "Higher Security", icon: "🔒" },
  { id: "faster_mvp", label: "Faster MVP", icon: "⚡" },
  { id: "regulated_industry", label: "Regulated Industry", icon: "⚖️" },
  { id: "fabric_first", label: "Fabric-First", icon: "🧵" },
];

/** Static description of the two-box "Architecture" flow (UI <-> Agentic
 * Workflow) - a conceptual overview, not agent/customer configuration, so
 * (like REDESIGN_GOALS/GOVERNANCE_POLICY_OPTIONS above) it's fine as UI
 * copy here rather than externalized config. */
const ARCHITECTURE_OVERVIEW_NODES: FlowDiagramNode[] = [
  {
    id: "ui",
    title: "UI",
    icon: "🖥️",
    description:
      "The customer-facing experience for this mission: React + Fluent UI screens that render forms, live agent activity, approvals, and generated results. A dedicated UI is generated per approved requirement in the Build phase.",
  },
  {
    id: "agentic-workflow",
    title: "Agentic Workflow",
    icon: "🤖",
    description:
      "The orchestrated set of Azure AI Foundry agents behind the UI: they discover requirements, design the architecture, generate build artifacts, and govern every decision made for this mission.",
  },
];

/** Icon per registered agent `role` (config/agents/registry.yaml) for the
 * Agentic Workflow diagram - purely a display affordance. */
const AGENT_ROLE_ICONS: Record<string, string> = {
  mission_orchestration: "🧭",
  requirement_discovery: "🔎",
  architecture_design: "🏗️",
  solution_build: "🛠️",
  solution_deployment: "🚀",
  governance: "🛡️",
  debugging: "🩺",
};

function iconForAgentRole(role: string): string {
  return AGENT_ROLE_ICONS[role] ?? "🧩";
}

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
  const { data: agents } = useAgentRegistry();

  /** The Agentic Workflow diagram shows the flow the Architecture Designer
   * set in motion for THIS mission's requirement scope - not Genie (the
   * internal mission orchestrator) - so its hub is whichever agent
   * actually produced this session's architecture recommendation (falling
   * back to the registered architecture_design agent before that
   * recommendation exists yet), and its spokes are that agent's own
   * `connected_agent_ids` (config/agents/registry.yaml): the Build,
   * Governance, and Deployment agents its design leads into. */
  const agenticWorkflow = useMemo(() => {
    if (!agents) return null;
    const architectureAgentId = snapshot?.components.find(
      (component) => component.step_id === "design-architecture",
    )?.recommended_by;
    const architectureAgent =
      (architectureAgentId ? agents.find((agent) => agent.id === architectureAgentId) : null) ??
      agents.find((agent) => agent.role === "architecture_design");
    if (!architectureAgent) return null;
    const spokes: FlowDiagramNode[] = (architectureAgent.connected_agent_ids ?? [])
      .map((agentId) => agents.find((agent) => agent.id === agentId))
      .filter((agent): agent is NonNullable<typeof agent> => Boolean(agent))
      .map((agent) => ({
        id: agent.id,
        title: agent.name,
        icon: iconForAgentRole(agent.role),
        description: agent.description,
      }));
    return {
      hub: {
        id: architectureAgent.id,
        title: architectureAgent.name,
        icon: iconForAgentRole(architectureAgent.role),
        description: architectureAgent.description,
      },
      spokes,
    };
  }, [agents, snapshot?.components]);

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
          <SectionCard title="🏛️ Architecture">
            <Text size={200} style={{ display: "block", marginBottom: 12, opacity: 0.7 }}>
              Hover a component to see what it's about.
            </Text>
            <InteractiveFlowDiagram nodes={ARCHITECTURE_OVERVIEW_NODES} />
          </SectionCard>
          <SectionCard title="🧭 Agentic Workflow">
            <Text size={200} style={{ display: "block", marginBottom: 12, opacity: 0.7 }}>
              Hover the Architecture Designer or any agent to see what it's about.
            </Text>
            <InteractiveFlowDiagram
              hub={agenticWorkflow?.hub}
              nodes={agenticWorkflow?.spokes ?? []}
              emptyLabel="Waiting on the Architecture Designer's recommendation..."
            />
          </SectionCard>
          {/* design-architecture's own recommendation is already shown in the
           * "Architecture" / "Agentic Workflow" diagrams above, so it's
           * excluded here to avoid a redundant duplicate card. */}
          {snapshot.components.filter((component) => component.step_id !== "design-architecture")
            .length === 0 ? (
            <Text size={300} style={{ opacity: 0.7 }}>
              No architecture components recommended yet.
            </Text>
          ) : (
            snapshot.components
              .filter((component) => component.step_id !== "design-architecture")
              .map((component) => (
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

    </div>
  );
}
