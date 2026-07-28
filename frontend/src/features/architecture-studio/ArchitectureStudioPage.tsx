import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
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
import { useAsyncResource } from "@/hooks/useAsyncResource";
import { approvalApi } from "@/services/approvalApi";
import { workflowApi } from "@/services/workflowApi";
import { getTraceId } from "@/state/traceRegistry";
import { PageHeader } from "@/layouts/AppShell";
import { LoadingState } from "@/components/LoadingState";
import { ErrorState } from "@/components/ErrorState";
import { SectionCard } from "@/components/SectionCard";
import { LiveWorkflowPulse } from "@/components/LiveWorkflowPulse";
import { AgentActivityAnimation } from "@/components/AgentActivityAnimation";
import { useWorkflowEventStream } from "@/hooks/useWorkflowEventStream";
import { ArchitectureComponentDiagram } from "./ArchitectureComponentDiagram";
import { splitTopLevelSections } from "@/utils/textArtifacts";
import type { ApiError } from "@/services/httpClient";

const REDESIGN_GOALS: Array<{ id: RedesignGoal; label: string; icon: string }> = [
  { id: "lower_cost", label: "Lower Cost", icon: "💰" },
  { id: "higher_security", label: "Higher Security", icon: "🔒" },
  { id: "faster_mvp", label: "Faster MVP", icon: "⚡" },
  { id: "regulated_industry", label: "Regulated Industry", icon: "⚖️" },
  { id: "fabric_first", label: "Fabric-First", icon: "🧵" },
];

/** Icon per top-level section of the architecture-designer's response
 * (config/prompts/registry.yaml's architecture-recommendation-v1 contract:
 * "## UI Design", "## Multi-Agent Workflow") - purely a display
 * affordance. */
const TOP_SECTION_ICONS: Array<[RegExp, string]> = [
  [/ui design/i, "🖥️"],
  [/multi-agent workflow/i, "🤖"],
];

/** These two sections are this mission's actual, requirement-derived
 * design output - the most important thing on this page - so they get the
 * animated glow-card + flow-diagram treatment the rest of the page
 * doesn't. */
const HIGHLIGHTED_SECTIONS = [/ui design/i, /multi-agent workflow/i];

function isHighlightedSection(title: string): boolean {
  return HIGHLIGHTED_SECTIONS.some((regex) => regex.test(title));
}

function iconForTopSection(title: string): string {
  for (const [regex, icon] of TOP_SECTION_ICONS) {
    if (regex.test(title)) return icon;
  }
  return "🧩";
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
  const { sessionId, workflowRunId, missionError, setMissionError } = useSessionContext();
  const navigate = useNavigate();
  const { data: snapshot, loading, error, refresh } = useArchitectureStudio(
    sessionId,
    workflowRunId,
    POLL_MS,
  );
  const { requestAlternative, requesting, error: reanalysisError } = useArchitectureReanalysis(
    sessionId,
    workflowRunId,
  );

  /** The Architecture Designer is given the approved requirements as its
   * design payload and asked to work out - using its own reasoning, not a
   * fixed lineup - how this specific scope of work translates into a
   * multi-agent solution (config/prompts/registry.yaml's
   * architecture-recommendation-v1). Its response is split into the two
   * top-level sections that contract requires, so this page shows THIS
   * mission's actual UI design and agent workflow instead of Genie's own
   * (fixed, content-independent) internal build pipeline. The UI/agent
   * hosting platform (Azure Static Web Apps / Azure AI Foundry) is fixed,
   * so no Azure infrastructure section is requested or rendered here -
   * filtered defensively in case an older run's cached output still has
   * one. */
  const architectureComponent = snapshot?.components.find(
    (component) => component.step_id === "design-architecture",
  );
  const topSections = useMemo(
    () =>
      (architectureComponent ? splitTopLevelSections(architectureComponent.content) : []).filter(
        (section) => !/azure reference architecture/i.test(section.title),
      ),
    [architectureComponent],
  );

  const approvalsFetcher = useCallback(
    () => (sessionId ? approvalApi.list(sessionId) : Promise.reject(new Error("No session"))),
    [sessionId],
  );
  const { data: approvals, refresh: refreshApprovals } = useAsyncResource(
    approvalsFetcher,
    [sessionId],
    { enabled: Boolean(sessionId) },
  );
  const { events: liveEvents, connected: liveConnected } = useWorkflowEventStream(sessionId);
  const lastLiveEvent = liveEvents[liveEvents.length - 1] ?? null;
  useEffect(() => {
    if (lastLiveEvent?.event_type === "step_completed" || lastLiveEvent?.event_type === "step_failed") {
      void refresh();
      void refreshApprovals();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastLiveEvent]);
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

      <LiveWorkflowPulse connected={liveConnected} events={liveEvents} />

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
          {!architectureComponent ? (
            missionError ? (
              <ErrorState
                error={missionError}
                onRetry={() => {
                  setMissionError(null);
                  navigate("/requirements");
                }}
              />
            ) : (
              <AgentActivityAnimation
                label="Genie is working with the Architecture Designer agent on this mission's UI design and multi-agent workflow..."
                events={liveEvents}
              />
            )
          ) : topSections.length > 0 ? (
            topSections.map((section) => {
              const highlighted = isHighlightedSection(section.title);
              return (
                <SectionCard
                  key={section.title}
                  highlight={highlighted}
                  title={
                    <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span className={highlighted ? "genie-sparkle" : undefined} style={{ fontSize: 18 }}>
                        {iconForTopSection(section.title)}
                      </span>
                      <span>{section.title}</span>
                    </span>
                  }
                >
                  {section.summary ? (
                    <Text size={200} style={{ display: "block", marginBottom: 12, opacity: 0.7 }}>
                      {section.summary}
                    </Text>
                  ) : null}
                  <ArchitectureComponentDiagram content={section.body} animated={highlighted} />
                </SectionCard>
              );
            })
          ) : (
            <SectionCard title="🏗️ Recommended Solution Architecture">
              <ArchitectureComponentDiagram content={architectureComponent.content} />
            </SectionCard>
          )}
          {snapshot.components
            .filter((component) => component.step_id !== "design-architecture")
            .map((component) => (
              <SectionCard
                key={component.step_id}
                title={`🏗️ ${component.step_id.replace(/-/g, " ")}`}
                action={<Text size={200}>{component.recommended_by}</Text>}
              >
                <ArchitectureComponentDiagram content={component.content} />
              </SectionCard>
            ))}
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
