import { useCallback, useEffect, useMemo, useState } from "react";
import { MessageBar, Spinner, Text } from "@fluentui/react-components";
import { useSessionContext } from "@/state/SessionContext";
import { useAsyncResource } from "@/hooks/useAsyncResource";
import { useWorkflowEventStream, workflowStepDeltaKey } from "@/hooks/useWorkflowEventStream";
import { governanceApi } from "@/services/governanceApi";
import { MISSION_PHASES, type MissionPhase } from "@/config/discoveryWorkflow";
import { summarizeAgentOutput } from "@/utils/textArtifacts";
import { sanitizePreview } from "@/utils/workflowEventText";
import type { GovernanceEvent } from "@/types/governance";
import type { WorkflowStreamEvent } from "@/types/workflowEvents";

function relativeTime(timestamp: string): string {
  const deltaMs = Date.now() - Date.parse(timestamp);
  if (Number.isNaN(deltaMs) || deltaMs < 0) return "";
  if (deltaMs < 1000) return "just now";
  if (deltaMs < 60_000) return `${Math.round(deltaMs / 1000)}s ago`;
  return `${Math.round(deltaMs / 60_000)}m ago`;
}

function stepId(detail: Record<string, unknown>): string | null {
  return typeof detail.step_id === "string" ? detail.step_id : null;
}

/** Real agent output preview recorded by the backend the instant that call completed. */
function governanceOutputPreview(detail: Record<string, unknown>): string | null {
  return typeof detail.output_preview === "string" ? detail.output_preview : null;
}

const PHASE_ICONS: Record<string, string> = {
  "analyze-requirements": "📋",
  "design-architecture": "🏗️",
  "build-solution": "🤖",
  "test-generation": "🧪",
};

type PhaseStatus = "pending" | "awaiting-proceed" | "running" | "completed" | "failed";

interface PhaseTrace {
  status: PhaseStatus;
  /** Real agent id to display - the live/persisted specialist id once
   * known, else this phase's configured default (see MissionPhase). */
  specialistId: string;
  /** How many `step_started` attempts this phase has made THIS session
   * (automatic retries - see backend WorkflowRuntime._MAX_STEP_RETRIES -
   * each produce their own step_started/step_failed or step_completed
   * pair, so a step failing then succeeding shows as attempt 2, 3, ...). */
  attempt: number;
  outputSummary: string | null;
  errorText: string | null;
  timestamp: string | null;
}

/**
 * Resolves each mission phase's current status/summary from two combined
 * sources: the session's persisted governance `agent_execution` events
 * (survive a page reload - only ever recorded on SUCCESS) and this
 * session's live SSE workflow-events stream (real-time `step_started`/
 * `step_completed`/`step_failed`, including genuine failures governance
 * never records). The SSE stream always wins once it has seen ANY event
 * for a phase this session, since it is strictly more current/detailed.
 */
function computePhaseTraces(
  phases: MissionPhase[],
  sseEvents: WorkflowStreamEvent[],
  stepDeltaText: Record<string, string>,
  governanceCompletedStepIds: Set<string>,
  governanceOutputByStep: Map<string, string>,
  governanceAgentByStep: Map<string, string>,
): PhaseTrace[] {
  const traces: PhaseTrace[] = [];
  let previousCompleted = true; // the first phase is free to start the instant the mission begins

  for (const phase of phases) {
    const stepEvents = sseEvents.filter((event) => event.step_id === phase.stepId);
    const latest = stepEvents.length > 0 ? stepEvents[stepEvents.length - 1] : null;
    const attempt = stepEvents.filter((event) => event.event_type === "step_started").length;
    const governanceCompleted = governanceCompletedStepIds.has(phase.stepId);

    let status: PhaseStatus;
    let outputSummary: string | null = null;
    let errorText: string | null = null;
    let specialistId = governanceAgentByStep.get(phase.stepId) ?? phase.specialistAgentId;
    let timestamp: string | null = null;

    const fullText = latest ? stepDeltaText[workflowStepDeltaKey(latest.step_id, latest.agent_id)] : undefined;

    if (latest?.event_type === "step_failed") {
      status = "failed";
      errorText = latest.error ?? "This step failed for an unknown reason.";
      specialistId = latest.agent_id;
      timestamp = latest.emitted_at;
    } else if (latest?.event_type === "step_completed" || (governanceCompleted && !latest)) {
      status = "completed";
      const previewText = latest?.output_preview ?? governanceOutputByStep.get(phase.stepId) ?? null;
      outputSummary =
        fullText && fullText.trim().length > 0
          ? summarizeAgentOutput(fullText)
          : previewText
            ? sanitizePreview(previewText, 260)
            : null;
      if (latest) {
        specialistId = latest.agent_id;
        timestamp = latest.emitted_at;
      }
    } else if (latest?.event_type === "step_started" || latest?.event_type === "step_delta") {
      status = "running";
      specialistId = latest.agent_id;
      timestamp = latest.emitted_at;
    } else if (!previousCompleted) {
      status = "pending";
    } else if (phase.requiresProceed) {
      status = "awaiting-proceed";
    } else {
      status = "pending";
    }

    traces.push({ status, specialistId, attempt, outputSummary, errorText, timestamp });
    previousCompleted = status === "completed";
  }

  return traces;
}

type FlowNodeStatus = "complete" | "active" | "pending" | "failed";

function toFlowNodeStatus(status: PhaseStatus): FlowNodeStatus {
  if (status === "failed") return "failed";
  if (status === "completed") return "complete";
  if (status === "running" || status === "awaiting-proceed") return "active";
  return "pending";
}

const FLOW_NODE_STYLE: Record<FlowNodeStatus, { border: string; background: string }> = {
  complete: { border: "#3fa66a", background: "rgba(63, 166, 106, 0.15)" },
  active: { border: "#d99a2b", background: "rgba(217, 154, 43, 0.15)" },
  pending: { border: "#2a323d", background: "#161c24" },
  failed: { border: "#d13438", background: "rgba(209, 52, 56, 0.18)" },
};

/** One glowing status node in the compact mission-phase overview strip. */
function FlowNode({ icon, status, label }: { icon: string; status: FlowNodeStatus; label: string }): JSX.Element {
  const nodeClass =
    status === "complete"
      ? "genie-stage-node-complete"
      : status === "active"
        ? "genie-stage-node-active"
        : status === "pending"
          ? "genie-stage-node-locked"
          : undefined;
  const { border, background } = FLOW_NODE_STYLE[status];
  return (
    <div
      className={nodeClass}
      title={label}
      style={{
        position: "relative",
        flexShrink: 0,
        width: 32,
        height: 32,
        borderRadius: "50%",
        border: `2px solid ${border}`,
        backgroundColor: background,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: 14,
      }}
    >
      {icon}
      {status === "complete" || status === "failed" ? (
        <span
          style={{
            position: "absolute",
            bottom: -3,
            right: -3,
            width: 14,
            height: 14,
            borderRadius: "50%",
            backgroundColor: status === "complete" ? "#3fa66a" : "#d13438",
            color: "#0b0f14",
            fontSize: 9,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          {status === "complete" ? "✓" : "!"}
        </span>
      ) : null}
    </div>
  );
}

/** Connector segment between two flow nodes; animates a traveling stripe while the mission is actively flowing into the next node. */
function FlowConnector({ state }: { state: FlowNodeStatus }): JSX.Element {
  return (
    <div
      className={state === "active" ? "genie-stage-line-active" : undefined}
      style={{
        flex: 1,
        height: 3,
        minWidth: 12,
        margin: "0 2px",
        borderRadius: 2,
        backgroundColor:
          state === "complete" ? "#3fa66a" : state === "failed" ? "#d13438" : state === "pending" ? "#232a33" : undefined,
        opacity: state === "pending" ? 0.6 : 1,
      }}
    />
  );
}

/** Horizontal map of the mission's phases - which has finished, which is running/blocked/failed, which is still ahead. */
function ControlFlowMap({ traces }: { traces: PhaseTrace[] }): JSX.Element {
  return (
    <div style={{ display: "flex", alignItems: "center", width: "100%" }}>
      {MISSION_PHASES.map((phase, index) => {
        const status = toFlowNodeStatus(traces[index].status);
        const isLast = index === MISSION_PHASES.length - 1;
        const nextStatus = !isLast ? toFlowNodeStatus(traces[index + 1].status) : null;
        const connectorState: FlowNodeStatus =
          status === "complete" ? (nextStatus === "active" || nextStatus === "failed" ? nextStatus : "complete") : "pending";
        return (
          <div key={phase.stepId} style={{ display: "flex", alignItems: "center", flex: isLast ? "0 0 auto" : 1 }}>
            <FlowNode icon={PHASE_ICONS[phase.stepId] ?? "🔹"} status={status} label={phase.label} />
            {!isLast ? <FlowConnector state={connectorState} /> : null}
          </div>
        );
      })}
    </div>
  );
}

/** One small line in the vertical mission trace (a trigger/gate/handoff marker, not a full phase card). */
function TraceLine({ icon, text, muted = false }: { icon: string; text: string; muted?: boolean }): JSX.Element {
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "flex-start", padding: "3px 0", opacity: muted ? 0.55 : 1 }}>
      <span style={{ fontSize: 13, lineHeight: "18px" }} aria-hidden="true">
        {icon}
      </span>
      <Text size={200}>{text}</Text>
    </div>
  );
}

const STATUS_BADGE: Record<PhaseStatus, { icon: string; label: string; color: string }> = {
  pending: { icon: "⏸️", label: "Not started", color: "#8a93a0" },
  "awaiting-proceed": { icon: "⏳", label: "Awaiting your proceed", color: "#d99a2b" },
  running: { icon: "▶️", label: "Running", color: "#2f83e0" },
  completed: { icon: "✅", label: "Completed", color: "#3fa66a" },
  failed: { icon: "❌", label: "Failed", color: "#d13438" },
};

/**
 * One phase's own trace card - its running/completed/failed status, the
 * real specialist agent handling it, and (once available) a clean output
 * summary or, on failure, the real error text. This is the "Requirement
 * (running) --> Output Summary" part of the requested UI --> Orchestrator
 * --> Phase --> Output Summary trace.
 */
function PhaseCard({ phase, trace }: { phase: MissionPhase; trace: PhaseTrace }): JSX.Element {
  const badge = STATUS_BADGE[trace.status];
  const borderColor = trace.status === "failed" ? "#d13438" : "#232a33";
  return (
    <div
      className="genie-fade-in"
      style={{
        border: `1px solid ${borderColor}`,
        borderRadius: 8,
        padding: "8px 10px",
        margin: "4px 0 8px 21px",
        backgroundColor: trace.status === "failed" ? "rgba(209, 52, 56, 0.08)" : "#161c24",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <Text size={200} weight="semibold">
          {PHASE_ICONS[phase.stepId] ?? "🔹"} {phase.label}
        </Text>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          {trace.status === "running" ? <Spinner size="tiny" /> : null}
          <Text size={100} style={{ color: badge.color, whiteSpace: "nowrap" }}>
            {trace.status === "running" ? "Running" : `${badge.icon} ${badge.label}`}
          </Text>
        </div>
      </div>
      {trace.attempt > 1 ? (
        <Text size={100} style={{ opacity: 0.65, display: "block", marginTop: 2 }}>
          🔁 Attempt {trace.attempt} (automatic retry)
        </Text>
      ) : null}
      {trace.status === "failed" ? (
        <MessageBar intent="error" layout="multiline" style={{ marginTop: 6 }}>
          {trace.errorText}
        </MessageBar>
      ) : trace.outputSummary ? (
        <Text size={200} style={{ opacity: 0.85, display: "block", marginTop: 4, whiteSpace: "pre-wrap" }}>
          {trace.outputSummary}
        </Text>
      ) : null}
      {trace.timestamp ? (
        <Text size={100} style={{ opacity: 0.5, display: "block", marginTop: 4 }}>
          {relativeTime(trace.timestamp)}
        </Text>
      ) : null}
    </div>
  );
}

/**
 * Floating "Mission Trace" overlay: a real, ordered UI -> Orchestrator ->
 * Phase (running/completed/failed) -> Output Summary trace, gated by
 * "Human: proceed" markers exactly where `WorkflowStep.requires_human_proceed`
 * pauses the run - so success AND errors are visible as they actually
 * happen, not just a raw feed of truncated code previews.
 *
 * Combines two data sources:
 * - The session's persisted governance `agent_execution` events (`GET
 *   /sessions/{id}/peer-review/events`) - survive a page reload, but are
 *   only ever recorded on SUCCESS.
 * - The live `GET /sessions/{id}/workflow-events/stream` SSE feed
 *   (`useWorkflowEventStream`) - real-time `step_started`/`step_completed`/
 *   `step_failed`, including genuine failures governance never records at
 *   all (see `WorkflowStepExecutor._run_agent`).
 */
export function TriagePanel({ enabled }: { enabled: boolean }): JSX.Element | null {
  const { sessionId, missionStartedAt } = useSessionContext();

  const eventsFetcher = useCallback(
    () =>
      sessionId ? governanceApi.listEvents(sessionId) : Promise.reject(new Error("No active session")),
    [sessionId],
  );
  const { data: events } = useAsyncResource(eventsFetcher, [sessionId], {
    enabled: enabled && Boolean(sessionId),
    pollIntervalMs: 2000,
  });

  const { events: sseEvents, stepDeltaText, connected } = useWorkflowEventStream(enabled ? sessionId : null);

  const specialistCalls = useMemo(
    () =>
      [...(events ?? [])].filter(
        (event): event is GovernanceEvent => event.category === "agent_execution" && event.detail.workflow_step !== true,
      ),
    [events],
  );

  const governanceCompletedStepIds = useMemo(() => {
    const ids = new Set<string>();
    for (const event of specialistCalls) {
      const id = stepId(event.detail);
      if (id) ids.add(id);
    }
    return ids;
  }, [specialistCalls]);

  const governanceOutputByStep = useMemo(() => {
    const map = new Map<string, string>();
    for (const event of specialistCalls) {
      const id = stepId(event.detail);
      const preview = governanceOutputPreview(event.detail);
      if (id && preview) map.set(id, preview);
    }
    return map;
  }, [specialistCalls]);

  const governanceAgentByStep = useMemo(() => {
    const map = new Map<string, string>();
    for (const event of specialistCalls) {
      const id = stepId(event.detail);
      if (id && event.agent_id) map.set(id, event.agent_id);
    }
    return map;
  }, [specialistCalls]);

  const traces = useMemo(
    () =>
      computePhaseTraces(
        MISSION_PHASES,
        sseEvents,
        stepDeltaText,
        governanceCompletedStepIds,
        governanceOutputByStep,
        governanceAgentByStep,
      ),
    [sseEvents, stepDeltaText, governanceCompletedStepIds, governanceOutputByStep, governanceAgentByStep],
  );

  const missionComplete = traces.length > 0 && traces.every((trace) => trace.status === "completed");
  const missionFailed = traces.some((trace) => trace.status === "failed");

  // Live "Xs elapsed" readout for the mission console header, ticking from
  // the moment the Upload page's "Start Prototyping" button was clicked
  // (shared via SessionContext) until every phase has completed.
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  useEffect(() => {
    if (!missionStartedAt || missionComplete) return;
    const tick = () => setElapsedSeconds(Math.round((Date.now() - missionStartedAt) / 1000));
    tick();
    const intervalId = window.setInterval(tick, 1000);
    return () => window.clearInterval(intervalId);
  }, [missionStartedAt, missionComplete]);

  if (!enabled) return null;

  return (
    <div
      className="genie-fade-in"
      role="complementary"
      aria-label="Agent mission traceability"
      style={{
        position: "fixed",
        top: 0,
        right: 0,
        bottom: 0,
        width: 360,
        height: "100vh",
        display: "flex",
        flexDirection: "column",
        backgroundColor: "#11161d",
        borderLeft: "1px solid #232a33",
        boxShadow: "-12px 0 32px rgba(0, 0, 0, 0.45)",
        overflow: "hidden",
        zIndex: 1000,
      }}
    >
      <div style={{ padding: "12px 16px", borderBottom: "1px solid #232a33" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
          <Text weight="bold" size={400}>
            🧭 Mission Trace
          </Text>
          {connected ? <span className="genie-live-dot" aria-label="Live" title="Live" /> : null}
        </div>
        <Text size={200} style={{ opacity: 0.7, display: "block" }}>
          UI → Orchestrator → each phase, in real order, with success and errors as they happen
        </Text>
      </div>

      <div
        style={{
          padding: "14px 16px",
          borderBottom: "1px solid #232a33",
          backgroundImage: "radial-gradient(circle at 0% 0%, rgba(47, 131, 224, 0.10), transparent 65%)",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
          <Text size={100} className="genie-stage-eyebrow" style={{ opacity: 0.65 }}>
            🧭 Mission Control Flow
          </Text>
          {missionStartedAt ? (
            <Text size={100} style={{ opacity: 0.6, whiteSpace: "nowrap" }}>
              {elapsedSeconds}s elapsed
            </Text>
          ) : null}
        </div>
        <ControlFlowMap traces={traces} />
        <Text size={200} style={{ display: "block", marginTop: 10, opacity: 0.85 }}>
          {missionFailed
            ? "❌ A phase failed - see details below"
            : missionComplete
              ? "✅ Mission complete"
              : missionStartedAt
                ? "⏳ Mission in progress"
                : "Awaiting mission start"}
        </Text>
      </div>

      <div style={{ overflowY: "auto", padding: "8px 16px 16px", flex: 1, minHeight: 0 }}>
        {!missionStartedAt ? (
          <Text size={200} style={{ opacity: 0.7, padding: 8, display: "block" }}>
            No mission activity yet - start a workflow run to see live traceability.
          </Text>
        ) : (
          <>
            <TraceLine icon="🖱️" text="UI: Start Prototyping clicked" />
            {MISSION_PHASES.map((phase, index) => {
              const trace = traces[index];
              const showGate = phase.requiresProceed;
              const gateCleared = trace.status === "running" || trace.status === "completed" || trace.status === "failed";
              return (
                <div key={phase.stepId}>
                  {showGate ? (
                    <TraceLine
                      icon={gateCleared ? "✅" : "⏳"}
                      text={gateCleared ? "Human: proceeded" : "Awaiting your review to proceed"}
                      muted={!gateCleared}
                    />
                  ) : null}
                  <TraceLine
                    icon="🧭"
                    text={`Orchestrator engaged → delegating to ${trace.specialistId || phase.specialistLabel}`}
                    muted={trace.status === "pending"}
                  />
                  <PhaseCard phase={phase} trace={trace} />
                </div>
              );
            })}
            {missionComplete ? <TraceLine icon="🏁" text="Mission complete - every phase finished." /> : null}
          </>
        )}
      </div>
    </div>
  );
}
