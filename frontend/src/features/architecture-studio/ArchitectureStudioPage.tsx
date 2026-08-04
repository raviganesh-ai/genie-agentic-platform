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
import { UiScreenShowcase } from "./UiScreenShowcase";
import { splitTopLevelSections, splitIntoNamedSections, parseUiScreenFlows } from "@/utils/textArtifacts";
import { ApiError } from "@/services/httpClient";
import type { SafeError } from "@/types/common";

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

/** Matches the architecture-recommendation-v1 contract's "## Multi-Agent
 * Workflow" section title - used to find that section's parsed agent list
 * so the user can deselect agents to limit the design. */
const MULTI_AGENT_WORKFLOW_TITLE = /multi-agent workflow/i;

/** Matches the architecture-recommendation-v1 contract's "## UI Design"
 * section title - used to parse its bullets into a real hub-and-spoke
 * Screen -> Orchestrator Agent flow diagram instead of raw prose. */
const UI_DESIGN_TITLE = /ui design/i;

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
  const { sessionId, workflowRunId, missionError, setMissionError, setGovernancePolicies } =
    useSessionContext();
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
  const liveEventsForRun = useMemo(
    () =>
      workflowRunId
        ? liveEvents.filter((event) => event.workflow_run_id === workflowRunId)
        : [],
    [liveEvents, workflowRunId],
  );
  const lastLiveEvent = liveEventsForRun[liveEventsForRun.length - 1] ?? null;
  useEffect(() => {
    if (lastLiveEvent?.event_type === "step_completed" || lastLiveEvent?.event_type === "step_failed") {
      void refresh();
      void refreshApprovals();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastLiveEvent]);
  // Latches permanently true the first time this run's architecture
  // component actually shows up - once that happens, the live "working..."
  // pulse must never come back for this page view again (e.g. while a
  // later "Re-run Architecture Studio" call is in flight), even though
  // `architectureComponent` itself briefly reflects stale/refreshing data.
  const [hasArchitectureOutput, setHasArchitectureOutput] = useState(false);
  useEffect(() => {
    if (architectureComponent) setHasArchitectureOutput(true);
  }, [architectureComponent]);
  const pendingArchitectureApproval = approvals?.find(
    (request) => request.status === "pending" && request.subject_id === "build-solution",
  );
  const [selectedPolicies, setSelectedPolicies] = useState<Record<string, boolean>>({});
  const [otherPolicyChecked, setOtherPolicyChecked] = useState(false);
  const [otherPolicyText, setOtherPolicyText] = useState("");
  const [approving, setApproving] = useState(false);
  const [approveError, setApproveError] = useState<string | null>(null);
  const [rerunningDesign, setRerunningDesign] = useState(false);
  const [rerunDesignError, setRerunDesignError] = useState<SafeError | null>(null);

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

  // Lets the user deselect specific agents from the "## Multi-Agent
  // Workflow" section - checked (included) by default. Approving directly
  // honors whatever is currently deselected here (see excludedAgentsText
  // below, forwarded to build-solution as its own step_input override):
  // the Build Agent generates no code at all for a deselected agent, and
  // neither the Orchestrator Agent nor the UI reference it. Separately,
  // "Regenerate without N agents" (further below) re-runs
  // design-architecture itself so the design TEXT/diagram also drops
  // those agents - the two are independent, not mutually required.
  const [excludedAgents, setExcludedAgents] = useState<Set<string>>(new Set());

  const excludedAgentsText = useMemo(() => Array.from(excludedAgents).join(", "), [excludedAgents]);

  const handleApproveArchitecture = useCallback(async () => {
    if (!sessionId || !workflowRunId || !pendingArchitectureApproval) return;
    setApproving(true);
    setApproveError(null);
    try {
      await approvalApi.decide(sessionId, pendingArchitectureApproval.id, "approved");
      const traceId = getTraceId(workflowRunId) ?? undefined;
      // Persisted in SessionContext (not just a local variable here) because
      // peer-review only actually executes in a LATER, separate resume
      // call - triggered from the Workshop page's "Proceed to Peer Review"
      // button - which needs to re-supply the same policies as its own
      // step_input override at that time.
      setGovernancePolicies(effectiveGovernancePolicies);
      // Move to the UI & Agent Design page immediately - that page has its
      // own live workflow event stream + polling and shows the "Genie is
      // calling the Orchestrator Agent..." animation until build-solution's
      // output arrives, then swaps in the generated artifacts. Resuming the
      // run itself can take a while (it runs build-solution and, once
      // approved, peer-review server-side), so we kick it off rather
      // than block navigation on it - any failure surfaces there via the
      // step's own recorded error instead of on this page the user has
      // already left.
      navigate("/workshop");
      workflowApi
        .resumeRun(sessionId, workflowRunId, traceId, {
          "build-solution": {
            step_id: "build-solution",
            variables: { policies: effectiveGovernancePolicies, excluded_agents: excludedAgentsText },
          },
        })
        .catch((err) => {
          console.error("Failed to resume the workflow after architecture approval.", err);
        });
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
    excludedAgentsText,
    navigate,
    setGovernancePolicies,
  ]);

  const handleRerunArchitectureStage = useCallback(async () => {
    if (!sessionId || !workflowRunId) return;
    setRerunningDesign(true);
    setRerunDesignError(null);
    try {
      const run = await workflowApi.getRun(sessionId, workflowRunId);
      const priorDesignStep = run.step_results.find((result) => result.step_id === "design-architecture");
      const approvedRequirements =
        priorDesignStep?.resolved_variables?.approved_requirements ??
        run.step_results.find((result) => result.step_id === "analyze-requirements")?.output_text ??
        "";
      const traceId = getTraceId(workflowRunId) ?? undefined;
      await workflowApi.resumeRun(sessionId, workflowRunId, traceId, {
        "design-architecture": {
          step_id: "design-architecture",
          variables: { approved_requirements: approvedRequirements },
        },
      });
      await Promise.all([refresh(), refreshApprovals()]);
    } catch (err) {
      setRerunDesignError(
        err instanceof ApiError ? err : { message: "Failed to re-run Architecture Studio." },
      );
    } finally {
      setRerunningDesign(false);
    }
  }, [sessionId, workflowRunId, refresh, refreshApprovals]);

  // Re-runs design-architecture (still the same workflow step, not a new
  // one) with a user_message asking the Architecture Designer to exclude
  // exactly those agents - honored by architecture-recommendation-v1's
  // exclude/deselect instruction - so the design TEXT/diagram itself also
  // drops them, not just the generated code. The step's approval
  // checkpoint was already granted earlier (Requirements page), so
  // re-executing it does not re-pause the run.
  const [regenerating, setRegenerating] = useState(false);
  const [regenerateError, setRegenerateError] = useState<SafeError | null>(null);

  const toggleAgentIncluded = useCallback((agentName: string, included: boolean) => {
    setExcludedAgents((prev) => {
      const next = new Set(prev);
      if (included) {
        next.delete(agentName);
      } else {
        next.add(agentName);
      }
      return next;
    });
  }, []);

  const handleRegenerateWithoutExcludedAgents = useCallback(async () => {
    if (!sessionId || !workflowRunId || excludedAgents.size === 0) return;
    setRegenerating(true);
    setRegenerateError(null);
    try {
      const run = await workflowApi.getRun(sessionId, workflowRunId);
      const priorDesignStep = run.step_results.find((result) => result.step_id === "design-architecture");
      const approvedRequirements =
        priorDesignStep?.resolved_variables?.approved_requirements ??
        run.step_results.find((result) => result.step_id === "analyze-requirements")?.output_text ??
        "";
      const traceId = getTraceId(workflowRunId) ?? undefined;
      await workflowApi.resumeRun(sessionId, workflowRunId, traceId, {
        "design-architecture": {
          step_id: "design-architecture",
          variables: {
            approved_requirements: approvedRequirements,
            user_message: `Exclude the following agents entirely from the design and adjust the multi-agent workflow so it still fully achieves the user's stated goal without them: ${Array.from(excludedAgents).join(", ")}.`,
          },
        },
      });
      await refresh();
      setExcludedAgents(new Set());
    } catch (err) {
      setRegenerateError(
        err instanceof ApiError ? err : { message: "Failed to regenerate the design." },
      );
    } finally {
      setRegenerating(false);
    }
  }, [sessionId, workflowRunId, excludedAgents, refresh]);

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

      {!hasArchitectureOutput ? (
        <LiveWorkflowPulse connected={liveConnected} events={liveEventsForRun} />
      ) : null}

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

      {architectureComponent ? (
        <SectionCard title="🔁 Re-run This Stage">
          <Text size={200} style={{ display: "block", marginBottom: 10, opacity: 0.75 }}>
            Re-execute Architecture Studio for this same workflow run using the current approved requirements.
          </Text>
          {rerunDesignError ? <ErrorState error={rerunDesignError} /> : null}
          <Button
            size="small"
            disabled={rerunningDesign}
            onClick={() => void handleRerunArchitectureStage()}
          >
            {rerunningDesign ? "Re-running Architecture Studio..." : "Re-run Architecture Studio"}
          </Button>
        </SectionCard>
      ) : null}

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
                events={liveEventsForRun}
              />
            )
          ) : topSections.length > 0 ? (
            topSections.map((section) => {
              const highlighted = isHighlightedSection(section.title);
              const isMultiAgentWorkflow = MULTI_AGENT_WORKFLOW_TITLE.test(section.title);
              const agentItems = isMultiAgentWorkflow ? splitIntoNamedSections(section.body) : [];
              const isUiDesign = UI_DESIGN_TITLE.test(section.title);
              // The architecture-recommendation-v1 contract requires every
              // UI Design bullet to also name the Orchestrator Agent (so the
              // UI is only ever shown calling that one agent) - but this
              // section should read as "what the UI looks like", not an
              // agent diagram, so only the screen name/description are kept
              // and the Orchestrator Agent name is deliberately dropped here
              // (it already has its own card in "## Multi-Agent Workflow"
              // below). Falls back to splitting plain named bullets when the
              // body doesn't use the "-->" contract (e.g. older/simpler
              // responses), so this section never falls back to the generic
              // agent-flow diagram.
              const uiScreens = isUiDesign
                ? (() => {
                    const flows = parseUiScreenFlows(section.body);
                    if (flows.length > 0) {
                      return flows.map((flow) => ({ title: flow.screen, description: flow.description }));
                    }
                    return splitIntoNamedSections(section.body).map((item) => ({
                      title: item.title,
                      description: item.body,
                    }));
                  })()
                : [];
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
                  {isUiDesign && uiScreens.length > 0 ? (
                    <UiScreenShowcase screens={uiScreens} />
                  ) : (
                    <ArchitectureComponentDiagram content={section.body} animated={highlighted} />
                  )}
                  {isMultiAgentWorkflow && agentItems.length > 1 ? (
                    <div style={{ marginTop: 16, paddingTop: 16, borderTop: "1px solid #232a33" }}>
                      <Text
                        size={200}
                        weight="semibold"
                        style={{ display: "block", marginBottom: 4, opacity: 0.75 }}
                      >
                        Limit the design - deselect any agents you don't need
                      </Text>
                      <Text size={200} style={{ display: "block", marginBottom: 8, opacity: 0.6 }}>
                        Deselected agents get no generated code in UI & Agent Design - approve
                        directly to apply this now, or regenerate below to also update the design
                        above.
                      </Text>
                      <div style={{ display: "flex", flexDirection: "column", gap: 4, marginBottom: 12 }}>
                        {agentItems.map((item) => (
                          <Checkbox
                            key={item.title}
                            label={item.title}
                            checked={!excludedAgents.has(item.title)}
                            disabled={regenerating}
                            onChange={(_, data) => toggleAgentIncluded(item.title, Boolean(data.checked))}
                          />
                        ))}
                      </div>
                      {regenerateError ? (
                        <div style={{ marginBottom: 12 }}>
                          <ErrorState error={regenerateError} />
                        </div>
                      ) : null}
                      <Button
                        size="small"
                        disabled={regenerating || excludedAgents.size === 0}
                        onClick={() => void handleRegenerateWithoutExcludedAgents()}
                      >
                        {regenerating
                          ? "Regenerating design..."
                          : `Regenerate without ${excludedAgents.size} agent${excludedAgents.size === 1 ? "" : "s"}`}
                      </Button>
                    </div>
                  ) : null}
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
