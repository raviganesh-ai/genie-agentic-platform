import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Badge, Button, MessageBar, MessageBarBody, MessageBarTitle, Text } from "@fluentui/react-components";
import { useSessionContext } from "@/state/SessionContext";
import { useAsyncResource } from "@/hooks/useAsyncResource";
import { deployLaunchApi } from "@/services/deployLaunchApi";
import { getTraceId } from "@/state/traceRegistry";
import { ApiError } from "@/services/httpClient";
import { PageHeader } from "@/layouts/AppShell";
import { LoadingState } from "@/components/LoadingState";
import { ErrorState } from "@/components/ErrorState";
import { SectionCard } from "@/components/SectionCard";
import { AgentActivityAnimation } from "@/components/AgentActivityAnimation";
import { useWorkflowEventStream } from "@/hooks/useWorkflowEventStream";
import { DEPLOYMENT_STEP_ORDER, DEPLOYMENT_STEP_NAMES } from "@/types/deployLaunch";
import type { DeploymentStepId, DeploymentStepResult, ProvisionedAgentStatus } from "@/types/deployLaunch";

const POLL_MS = 4000;

const STEP_STATUS_COLORS: Record<DeploymentStepResult["status"], string> = {
  pending: "#8a8f98",
  running: "#d99a2b",
  completed: "#3fa66a",
  failed: "#d1495b",
  skipped: "#8a8f98",
};

// The user-facing status vocabulary is deliberately "Started → In Progress →
// Deployed" for every phase (and per-agent row) rather than generic
// pending/running/completed test jargon.
const STEP_STATUS_LABELS: Record<DeploymentStepResult["status"], string> = {
  pending: "Not Started",
  running: "In Progress…",
  completed: "✅ Deployed",
  failed: "❌ Failed",
  skipped: "Skipped",
};

const AGENT_STATUS_LABELS: Record<ProvisionedAgentStatus["status"], string> = {
  pending: "Queued",
  running: "Deploying…",
  completed: "✅ Deployed",
  failed: "❌ Failed",
  skipped: "Skipped",
};

// Narrative, "gamified" copy for the `AgentActivityAnimation` banner shown
// while a step is actively running (or while the pipeline is still getting
// started) - so the user always sees a concrete "Genie is working with..."
// message rather than a silent, static "Not Started" list. Mirrors the same
// convention used on Requirement Discovery/Workshop.
const STEP_WORKING_LABELS: Record<DeploymentStepId, string> = {
  "generate-access-policy": "Genie is working with the Orchestrator to generate your least-access policy...",
  "provision-foundry-agents": "Genie is working with Azure AI Foundry to deploy your mission agents...",
  "deploy-backend-service": "Genie is working with the Orchestrator to deploy your backend service...",
  "sync-frontend-integration": "Genie is wiring your frontend to the newly deployed backend...",
  "deploy-frontend-app": "Genie is publishing your frontend application...",
  "generate-test-suite": "Genie is working with the Orchestrator to write functional & regression tests...",
  "execute-test-suite": "Genie is running your full functional & regression test suite...",
  "run-security-scan": "Genie is scanning your backend and frontend for security issues...",
  "launch-mission": "Genie is minting your customer-facing launch link...",
};
const STARTING_LABEL =
  "Genie is working with the Orchestrator to get your deployment started - this can take a minute...";

// A distinct emoji per pipeline step - purely decorative/visual variety for
// the mission flow map and step rows below, mirrors the same convention as
// Triage's `PHASE_ICONS`. Never affects step identity/ordering, which is
// still driven entirely by `DEPLOYMENT_STEP_ORDER`/`DEPLOYMENT_STEP_NAMES`.
const STEP_ICONS: Record<DeploymentStepId, string> = {
  "generate-access-policy": "🔐",
  "provision-foundry-agents": "🤖",
  "deploy-backend-service": "⚙️",
  "sync-frontend-integration": "🔗",
  "deploy-frontend-app": "🌐",
  "generate-test-suite": "🧪",
  "execute-test-suite": "✅",
  "run-security-scan": "🛡️",
  "launch-mission": "🚀",
};

/** A short "12s"/"1m 4s" duration readout between a step's real started_at
 * and completed_at timestamps - omitted entirely when either is missing so
 * no fabricated timing is ever shown. */
function formatDuration(startedAt: string | null, completedAt: string | null): string | null {
  if (!startedAt || !completedAt) return null;
  const deltaMs = Date.parse(completedAt) - Date.parse(startedAt);
  if (!Number.isFinite(deltaMs) || deltaMs < 0) return null;
  const totalSeconds = Math.round(deltaMs / 1000);
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}m ${seconds}s`;
}

type FlowNodeState = "complete" | "active" | "failed" | "locked";

/** One glowing status node in the mission flow map - reuses the exact same
 * node/connector visual language (`genie-stage-node-*`, `genie-stage-line-active`)
 * as the AppShell sidebar and Triage panel's control-flow map, so the "skill
 * tree" motif reads identically everywhere it appears in Genie. */
function FlowMapNode({ icon, label, state }: { icon: string; label: string; state: FlowNodeState }): JSX.Element {
  const nodeClass =
    state === "complete"
      ? "genie-stage-node-complete"
      : state === "active"
        ? "genie-stage-node-active"
        : state === "failed"
          ? undefined
          : "genie-stage-node-locked";
  const borderColor =
    state === "complete" ? "#3fa66a" : state === "active" ? "#d99a2b" : state === "failed" ? "#d1495b" : "#2a323d";
  const backgroundColor =
    state === "complete"
      ? "rgba(63, 166, 106, 0.15)"
      : state === "active"
        ? "rgba(217, 154, 43, 0.15)"
        : state === "failed"
          ? "rgba(209, 73, 91, 0.15)"
          : "#161c24";
  return (
    <div
      className={nodeClass}
      title={label}
      aria-label={label}
      style={{
        position: "relative",
        flexShrink: 0,
        width: 36,
        height: 36,
        borderRadius: "50%",
        border: `2px solid ${borderColor}`,
        backgroundColor,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: 16,
      }}
    >
      {icon}
      {state === "complete" ? (
        <span
          style={{
            position: "absolute",
            bottom: -3,
            right: -3,
            width: 15,
            height: 15,
            borderRadius: "50%",
            backgroundColor: "#3fa66a",
            color: "#0b0f14",
            fontSize: 9,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          ✓
        </span>
      ) : null}
    </div>
  );
}

/** Connector segment between two flow map nodes; animates a traveling
 * stripe while the mission is actively flowing into the next node. */
function FlowMapConnector({ state }: { state: FlowNodeState }): JSX.Element {
  return (
    <div
      className={state === "active" ? "genie-stage-line-active" : undefined}
      style={{
        flex: 1,
        height: 3,
        minWidth: 10,
        margin: "0 2px",
        borderRadius: 2,
        backgroundColor:
          state === "complete" ? "#3fa66a" : state === "failed" ? "#d1495b" : state === "locked" ? "#232a33" : undefined,
        opacity: state === "locked" ? 0.6 : 1,
      }}
    />
  );
}

/** Horizontal "skill tree" style overview of all nine Deploy & Launch
 * phases - a compact, game-like map of the whole mission at a glance,
 * complementing (not replacing) the detailed per-step list below it.
 * Callers pass already-adjusted `steps` (see `displaySteps` in
 * `DeployLaunchPage`, which optimistically reports the single next
 * not-yet-started step as "running" while the mission is active) so this
 * map and the detailed step-row list below always agree on which step is
 * currently "live". */
function MissionFlowMap({ steps }: { steps: DeploymentStepResult[] }): JSX.Element {
  return (
    <div style={{ display: "flex", alignItems: "center", width: "100%", padding: "4px 2px" }}>
      {steps.map((step, index) => {
        const state: FlowNodeState =
          step.status === "completed"
            ? "complete"
            : step.status === "failed"
              ? "failed"
              : step.status === "running"
                ? "active"
                : "locked";
        const isLast = index === steps.length - 1;
        const nextState: FlowNodeState =
          step.status === "completed" && steps[index + 1]?.status === "running" ? "active" : state;
        return (
          <div key={step.step_id} style={{ display: "flex", alignItems: "center", flex: isLast ? "0 0 auto" : 1 }}>
            <FlowMapNode icon={STEP_ICONS[step.step_id]} label={DEPLOYMENT_STEP_NAMES[step.step_id]} state={state} />
            {!isLast ? <FlowMapConnector state={nextState} /> : null}
          </div>
        );
      })}
    </div>
  );
}

function AgentRow({ agent }: { agent: ProvisionedAgentStatus }): JSX.Element {
  const color = STEP_STATUS_COLORS[agent.status];
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 12,
        borderLeft: `3px solid ${color}`,
        padding: "6px 10px",
        background: "rgba(255,255,255,0.03)",
        borderRadius: 4,
      }}
    >
      <Text size={200} weight="semibold">
        {agent.agent_name}
      </Text>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        {agent.foundry_agent_name ? (
          <Text size={200} style={{ opacity: 0.75, fontFamily: "monospace" }}>
            {agent.foundry_agent_name}
          </Text>
        ) : null}
        <Text size={200} style={{ color }}>
          {AGENT_STATUS_LABELS[agent.status]}
        </Text>
      </div>
    </div>
  );
}

function StepRow({
  step,
  agents,
  onRetry,
  isRetrying,
}: {
  step: DeploymentStepResult;
  agents?: ProvisionedAgentStatus[];
  onRetry?: (stepId: DeploymentStepId) => Promise<void>;
  isRetrying?: boolean;
}): JSX.Element {
  const color = STEP_STATUS_COLORS[step.status];
  const duration = formatDuration(step.started_at, step.completed_at);
  const isRunning = step.status === "running";
  return (
    <div
      className={isRunning ? "genie-agent-activity" : undefined}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 4,
        border: `1px solid ${color}`,
        borderRadius: 6,
        padding: "8px 12px",
        boxShadow: isRunning ? "0 0 0 1px rgba(217, 154, 43, 0.25), 0 0 14px 1px rgba(217, 154, 43, 0.18)" : "none",
        transition: "box-shadow 200ms ease, border-color 200ms ease",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <Text size={300} weight="semibold">
          <span aria-hidden="true" style={{ marginRight: 8 }}>
            {STEP_ICONS[step.step_id]}
          </span>
          {DEPLOYMENT_STEP_NAMES[step.step_id]}
        </Text>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {duration ? (
            <Text size={100} style={{ opacity: 0.55, fontFamily: "monospace" }}>
              {duration}
            </Text>
          ) : null}
          <Text size={200} style={{ color }}>
            {STEP_STATUS_LABELS[step.status]}
          </Text>
          {step.status === "failed" && onRetry ? (
            <Button
              size="small"
              appearance="subtle"
              disabled={isRetrying}
              onClick={() => void onRetry(step.step_id)}
              style={{ marginLeft: 8 }}
            >
              {isRetrying ? "Retrying..." : "Retry"}
            </Button>
          ) : null}
        </div>
      </div>
      {step.detail ? (
        <Text size={200} style={{ opacity: 0.8, whiteSpace: "pre-wrap" }}>
          {step.detail}
        </Text>
      ) : null}
      {step.error ? (
        <Text size={200} style={{ color: "#d1495b" }}>
          {step.error}
        </Text>
      ) : null}
      {agents && agents.length > 0 ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 4 }}>
          {agents.map((agent) => (
            <AgentRow key={agent.agent_name} agent={agent} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

/**
 * The real Deploy & Launch pipeline: nine named, code-driven steps
 * (`DEPLOYMENT_STEP_ORDER`) executed by the backend's
 * `DeploymentPipelineService` against real Azure SDKs (or their Null/local
 * equivalents in local provider mode) - never simulated. Starts
 * automatically as soon as this page loads with no run yet for this
 * mission - the user's review already happened on Workshop (the checkbox +
 * "Proceed to Deploy & Launch" action), so no separate manual click or
 * approval screen is needed here. This stage has exactly one gate - the
 * human already having clicked through to get here - so `start()` runs the
 * pipeline immediately. The backend also self-heals any not-yet-finished
 * upstream workflow step (e.g. build-solution/test-generation) by resuming
 * the same run before running the pipeline.
 */
export function DeployLaunchPage(): JSX.Element {
  const { sessionId, workflowRunId } = useSessionContext();

  const runsFetcher = useCallback(
    () => (sessionId ? deployLaunchApi.list(sessionId) : Promise.reject(new Error("No active session"))),
    [sessionId],
  );
  const { data: runs, loading, error, refresh } = useAsyncResource(runsFetcher, [sessionId], {
    enabled: Boolean(sessionId),
    pollIntervalMs: POLL_MS,
  });

  const activeRun = useMemo(() => {
    if (!runs || runs.length === 0) return null;
    return [...runs].sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  }, [runs]);

  const { events: liveEvents, connected: liveConnected } = useWorkflowEventStream(sessionId);
  const lastLiveEvent = liveEvents[liveEvents.length - 1] ?? null;
  useEffect(() => {
    if (lastLiveEvent?.event_type === "step_completed" || lastLiveEvent?.event_type === "step_failed") {
      refresh();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastLiveEvent]);

  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

  // A client-side network hiccup on the start() call (e.g. a slow/lost
  // response) does not mean the pipeline itself failed to kick off - the
  // request may well have reached the server and created the run. Once
  // polling proves a non-failed run actually exists for this mission, the
  // stale "couldn't reach the backend" banner must not keep showing over a
  // run that is actually in progress or has already succeeded.
  useEffect(() => {
    if (startError && activeRun && activeRun.status !== "failed") {
      setStartError(null);
    }
  }, [startError, activeRun]);

  const handleStart = useCallback(async () => {
    if (!sessionId || !workflowRunId) return;
    setStarting(true);
    setStartError(null);
    const traceId = getTraceId(workflowRunId) ?? undefined;
    
    // When retrying after a failure, resume from the first failed step instead of starting from the beginning
    let resumeFromStep: string | undefined;
    if (activeRun?.status === "failed") {
      const firstFailedStep = activeRun.steps.find((step) => step.status === "failed");
      if (firstFailedStep) {
        resumeFromStep = firstFailedStep.step_id;
      }
    }
    
    try {
      await deployLaunchApi.start(sessionId, workflowRunId, traceId, resumeFromStep);
      refresh();
    } catch (err) {
      setStartError((err as ApiError).message ?? "Failed to start Deploy & Launch.");
    } finally {
      setStarting(false);
    }
  }, [sessionId, workflowRunId, activeRun, refresh]);

  // Starts Deploy & Launch automatically the first time this page has no
  // run yet for the current mission - the user's review already happened
  // on Workshop, so no separate manual "Start Deploy & Launch" click is
  // needed for the normal flow. Fires once per mount/run-set; the button
  // below still exists to manually retry a genuine failure.
  const autoStartedRef = useRef(false);
  useEffect(() => {
    if (!sessionId || !workflowRunId) return;
    if (!runs) return;
    if (activeRun) return;
    if (autoStartedRef.current) return;
    autoStartedRef.current = true;
    void handleStart();
  }, [sessionId, workflowRunId, runs, activeRun, handleStart]);

  // The full step roster, shown to the user immediately - even before a run
  // has actually started - so they see the whole plan up front ("Not
  // Started" for every step) rather than a generic spinner, then watch each
  // step's status update in place as the pipeline actually executes.
  const steps: DeploymentStepResult[] = useMemo(
    () =>
      DEPLOYMENT_STEP_ORDER.map(
        (stepId) =>
          activeRun?.steps.find((candidate) => candidate.step_id === stepId) ?? {
            step_id: stepId,
            name: DEPLOYMENT_STEP_NAMES[stepId],
            status: "pending" as const,
            detail: "",
            error: null,
            started_at: null,
            completed_at: null,
          },
      ),
    [activeRun],
  );

  // Overall mission progress - drives the "skill tree" flow map and the
  // progress bar/badge above the detailed step list. Purely derived from
  // the real step statuses above, never a separate/fabricated counter.
  const completedStepCount = useMemo(() => steps.filter((step) => step.status === "completed").length, [steps]);
  const progressPct = Math.round((completedStepCount / steps.length) * 100);

  // Drives both the activity banner and the optimistic "next step is
  // running" override below. Deliberately also covers the pre-run window -
  // `runs` has loaded but no run exists yet - because the page auto-starts
  // the pipeline in that exact state (see the auto-start effect above), and
  // `starting` is only true while the POST itself is in flight. Without
  // that third clause the page falls back to a silent wall of "Not Started"
  // twice: once before the auto-start effect fires, and again between
  // `handleStart` clearing `starting` and the `refresh()` result landing.
  const isPipelineActive =
    !startError &&
    activeRun?.status !== "failed" &&
    (starting || activeRun?.status === "running" || (!!runs && !activeRun));

  // Deploy & Launch's own `start()` first self-heals any not-yet-finished
  // upstream workflow step (e.g. build-solution resumed because Workshop's
  // "Proceed" is a client-side gesture, not a wait for the backend's own,
  // slower official step completion - see pipeline_service.py's
  // `_ensure_upstream_steps_completed`) BEFORE this pipeline's own nine
  // steps begin - real step 1 genuinely cannot start until that resume
  // finishes, which can legitimately take minutes for a full build
  // regeneration. Past a short grace window, treat "every one of our own
  // steps is still pending" as evidence we are still waiting on that
  // upstream work, not evidence step 1 is "about to start any second" -
  // otherwise the optimistic override below keeps lying (a step 1 badge
  // stuck on "In Progress" for many minutes while genie-orchestrator is
  // actually still finishing an earlier mission phase).
  const noOwnStepHasStartedYet = steps.every((step) => step.status === "pending");
  const runAgeMs = activeRun ? Date.now() - Date.parse(activeRun.created_at) : 0;
  const awaitingUpstreamStep = isPipelineActive && noOwnStepHasStartedYet && runAgeMs > 20_000;

  // What the user actually sees rendered (flow map + step-row list): while
  // the mission is genuinely in motion, the single next not-yet-started step
  // is optimistically shown as "In Progress" rather than "Not Started" -
  // Genie really is working on it server-side the moment the prior step
  // completes (or from the very start for step 1), the backend's own status
  // field for it just hasn't flipped to "running" yet (that requires its
  // first `step_started` event/poll to land). Never overrides a real
  // completed/failed/running status - purely fills the "about to start"
  // gap so the whole page never looks frozen on a wall of "Not Started".
  // Suppressed entirely while `awaitingUpstreamStep` is true - see above.
  const displaySteps = useMemo(() => {
    if (!isPipelineActive || awaitingUpstreamStep) return steps;
    const nextIndex = steps.findIndex(
      (step) => step.status !== "completed" && step.status !== "failed" && step.status !== "running",
    );
    if (nextIndex === -1) return steps;
    return steps.map((step, index) => (index === nextIndex ? { ...step, status: "running" as const } : step));
  }, [steps, isPipelineActive, awaitingUpstreamStep]);

  const flowSteps = useMemo(() => {
    if (!awaitingUpstreamStep) return displaySteps;
    return displaySteps.map((step, index) =>
      index === 0 ? { ...step, status: "running" as const } : step,
    );
  }, [awaitingUpstreamStep, displaySteps]);

  // Drives the gamified "Genie is working with..." activity banner: while
  // the pipeline is genuinely in motion (either the start() request is
  // still in flight, or a run exists and is running) but no error/failure
  // is showing, surface the currently-running step's narrative label (or a
  // generic "getting started" label before the first step has flipped to
  // running) so the user always sees concrete evidence of progress instead
  // of a silent, static "Not Started" list. While genuinely still waiting
  // on an earlier mission step (see `awaitingUpstreamStep`), show an
  // honest "finishing an earlier step" message instead of falsely
  // attributing activity to this pipeline's own step 1 - the real,
  // still-live event text below this label (`AgentActivityAnimation`'s own
  // `events` prop) already shows what is actually happening.
  const runningStep = useMemo(() => displaySteps.find((step) => step.status === "running") ?? null, [displaySteps]);
  const activityLabel = awaitingUpstreamStep
    ? "Genie is finishing an earlier mission step before Deploy & Launch's own steps can begin..."
    : !activeRun
      ? // No run record exists yet (still auto-starting) - naming step 1's
        // specific work here would claim progress that has not begun.
        STARTING_LABEL
      : runningStep
        ? STEP_WORKING_LABELS[runningStep.step_id]
        : STARTING_LABEL;

  // Opens the mission's real, deployed launch URL in a brand-new browser
  // tab/window - never navigates the Genie platform itself away from this
  // page. `noopener,noreferrer` prevents the newly opened page from getting
  // a handle back to this window (standard tab-nabbing protection).
  const handleLaunch = useCallback(() => {
    if (!activeRun?.launch_url) return;
    window.open(activeRun.launch_url, "_blank", "noopener,noreferrer");
  }, [activeRun]);

  const [retryingStep, setRetryingStep] = useState<DeploymentStepId | null>(null);
  const [retryError, setRetryError] = useState<string | null>(null);

  const handleRetryStep = useCallback(
    async (stepId: DeploymentStepId) => {
      if (!sessionId || !workflowRunId) return;
      setRetryingStep(stepId);
      setRetryError(null);
      const traceId = getTraceId(workflowRunId) ?? undefined;
      try {
        await deployLaunchApi.start(sessionId, workflowRunId, traceId, stepId);
        refresh();
      } catch (err) {
        setRetryError((err as ApiError).message ?? `Failed to retry ${DEPLOYMENT_STEP_NAMES[stepId]}.`);
      } finally {
        setRetryingStep(null);
      }
    },
    [sessionId, workflowRunId, refresh],
  );

  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const handleDownload = useCallback(async () => {
    if (!sessionId || !activeRun) return;
    setDownloading(true);
    setDownloadError(null);
    try {
      const { blob, filename } = await deployLaunchApi.download(sessionId, activeRun.id);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setDownloadError((err as ApiError).message ?? "Failed to download the build.");
    } finally {
      setDownloading(false);
    }
  }, [sessionId, activeRun]);

  if (!sessionId || !workflowRunId) {
    return (
      <div>
        <PageHeader title="Deploy & Launch" subtitle="No active mission yet." />
        <Text size={300} style={{ opacity: 0.7 }}>
          Complete the UI & Agent Design workshop from an active mission run before deploying.
        </Text>
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Deploy & Launch"
        subtitle="Provisions access control, agents, backend/frontend, runs full testing and a security scan, then mints the customer-facing launch link."
      />
      {loading && !runs ? <LoadingState label="Loading Deploy & Launch status..." /> : null}
      {error ? <ErrorState error={error} onRetry={refresh} /> : null}

      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        {/* No manual click/confirmation is shown for the normal path - the
            auto-start effect above already kicks this off the instant the
            page loads with no run yet. A visible "Start Deploy & Launch"
            button only appears when something genuinely needs the user's
            action: a real start failure (startError) or a run that already
            failed - never as a routine second confirmation after Workshop's
            review. */}
        {startError || activeRun?.status === "failed" ? (
          <SectionCard title={activeRun?.status === "failed" ? "Deploy & Launch Failed" : "Start Deploy & Launch"}>
            {startError ? (
              <MessageBar intent="warning" layout="multiline" style={{ marginBottom: 12 }}>
                <MessageBarBody>
                  <MessageBarTitle>Deploy & Launch</MessageBarTitle>
                  {startError}
                </MessageBarBody>
              </MessageBar>
            ) : null}
            <div style={{ display: "flex", gap: 8 }}>
              <Button appearance="primary" disabled={starting} onClick={() => void handleStart()}>
                {starting ? "Starting..." : "Retry Deploy & Launch"}
              </Button>
            </div>
          </SectionCard>
        ) : null}

        {isPipelineActive ? (
          <AgentActivityAnimation label={activityLabel} events={liveEvents} startedAt={activeRun?.created_at} />
        ) : null}

        {awaitingUpstreamStep ? (
          <div
            className="genie-upstream-build-progress"
            style={{
              border: "1px solid #2f83e055",
              borderLeft: "4px solid #2f83e0",
              borderRadius: 6,
              padding: "12px 14px",
              backgroundColor: "rgba(47, 131, 224, 0.08)",
            }}
          >
            <Text weight="semibold" size={300} style={{ display: "block", marginBottom: 4 }}>
              Build solution is still running
            </Text>
            <Text size={200} style={{ display: "block", opacity: 0.75 }}>
              Deployment begins automatically as soon as generated UI and agent code are ready.
            </Text>
            <div className="genie-indeterminate-rail" aria-label="Build in progress" />
          </div>
        ) : null}

        <SectionCard
          title="🎮 Mission Progress"
          action={
            <Badge
              shape="rounded"
              style={{ backgroundColor: progressPct === 100 ? "#3fa66a" : "#2f83e0", color: "#0b0f14" }}
            >
              {completedStepCount}/{steps.length} Phases Complete
            </Badge>
          }
        >
          <MissionFlowMap steps={flowSteps} />
          <div
            style={{
              height: 8,
              borderRadius: 4,
              backgroundColor: "#232a33",
              overflow: "hidden",
              marginTop: 12,
              marginBottom: 16,
            }}
          >
            <div
              className="genie-xp-bar"
              style={{
                height: "100%",
                width: `${progressPct}%`,
                backgroundColor: progressPct === 100 ? "#3fa66a" : "#2f83e0",
              }}
            />
          </div>
          {retryError ? (
            <MessageBar intent="warning" layout="multiline" style={{ marginBottom: 12 }}>
              <MessageBarBody>
                <MessageBarTitle>Retry Failed</MessageBarTitle>
                {retryError}
              </MessageBarBody>
            </MessageBar>
          ) : null}
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {displaySteps.map((step) => {
              const agents =
                step.step_id === "provision-foundry-agents" ? activeRun?.provisioned_agents ?? [] : undefined;
              return (
                <StepRow
                  key={step.step_id}
                  step={step}
                  agents={agents}
                  onRetry={handleRetryStep}
                  isRetrying={retryingStep === step.step_id}
                />
              );
            })}
          </div>
        </SectionCard>

        {activeRun ? (
          <>
            {activeRun.access_policy ? (
              <SectionCard title="🔐 Least-Access Policy">
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  {activeRun.access_policy.agents.map((agent) => (
                    <Text key={agent.agent_id} size={300}>
                      <b>{agent.agent_id}</b> ({agent.role}) — tools: {agent.allowed_tools.join(", ") || "none"};
                      memory: {agent.memory_access.join(", ") || "none"}
                    </Text>
                  ))}
                </div>
              </SectionCard>
            ) : null}

            {activeRun.test_summary || activeRun.security_findings_count !== null ? (
              <SectionCard
                title="🧪 Testing & Security"
                action={
                  <div style={{ display: "flex", gap: 6 }}>
                    {activeRun.test_summary ? (
                      <Badge shape="rounded" style={{ backgroundColor: "#3fa66a", color: "#0b0f14" }}>
                        ✅ Tests Passed
                      </Badge>
                    ) : null}
                    {activeRun.security_findings_count !== null ? (
                      <Badge
                        shape="rounded"
                        style={{
                          backgroundColor: activeRun.security_findings_count === 0 ? "#3fa66a" : "#d99a2b",
                          color: "#0b0f14",
                        }}
                      >
                        🛡️ {activeRun.security_findings_count} Finding
                        {activeRun.security_findings_count === 1 ? "" : "s"}
                      </Badge>
                    ) : null}
                  </div>
                }
              >
                {activeRun.test_summary ? (
                  <Text size={300} style={{ whiteSpace: "pre-wrap", display: "block", marginBottom: 8 }}>
                    {activeRun.test_summary}
                  </Text>
                ) : null}
                {activeRun.security_findings_count !== null ? (
                  <Text size={300}>Security scan findings: {activeRun.security_findings_count}</Text>
                ) : null}
              </SectionCard>
            ) : null}

            {activeRun.status === "completed" && activeRun.launch_url ? (
              <SectionCard title="🎉 Mission Launched!" highlight>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
                  <span className="genie-sparkle" aria-hidden="true" style={{ fontSize: 22 }}>
                    🧞
                  </span>
                  <Badge shape="rounded" style={{ backgroundColor: "#3fa66a", color: "#0b0f14" }}>
                    ✅ Deployment Complete
                  </Badge>
                  <span className="genie-sparkle" aria-hidden="true" style={{ fontSize: 18 }}>
                    ✨
                  </span>
                </div>
                <Text size={300} style={{ display: "block", marginBottom: 12 }}>
                  Your solution is live at:{" "}
                  <a href={activeRun.launch_url} target="_blank" rel="noreferrer">
                    {activeRun.launch_url}
                  </a>
                </Text>
                {downloadError ? <ErrorState error={{ message: downloadError }} /> : null}
                <div style={{ display: "flex", gap: 8 }}>
                  <Button appearance="primary" onClick={handleLaunch}>
                    Launch
                  </Button>
                  <Button disabled={downloading} onClick={() => void handleDownload()}>
                    {downloading ? "Preparing download..." : "Download Code & Access Policy"}
                  </Button>
                </div>
              </SectionCard>
            ) : null}
          </>
        ) : null}

        {!liveConnected && activeRun && activeRun.status === "running" ? (
          <Text size={200} style={{ opacity: 0.6 }}>
            Live updates disconnected - still polling every {POLL_MS / 1000}s.
          </Text>
        ) : null}
      </div>
    </div>
  );
}
