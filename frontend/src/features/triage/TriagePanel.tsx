import { useCallback, useMemo } from "react";
import { Badge, Text } from "@fluentui/react-components";
import { useSessionContext } from "@/state/SessionContext";
import { useAsyncResource } from "@/hooks/useAsyncResource";
import { governanceApi } from "@/services/governanceApi";
import { MISSION_PHASES } from "@/config/discoveryWorkflow";
import type { GovernanceEvent } from "@/types/governance";

/**
 * Deterministic emoji chosen per `agent_id` (via a simple string hash) so
 * every agent gets a stable, distinct icon in the feed without the UI
 * hardcoding any specific agent id from the registry - keeps this panel
 * safe against future agent/workflow reconfiguration.
 */
const AGENT_ICONS = ["🤖", "🛰️", "🧭", "🛠️", "🔎", "🧩", "📡", "🧠"];

function iconForAgent(agentId: string): string {
  let hash = 0;
  for (let i = 0; i < agentId.length; i += 1) {
    hash = (hash * 31 + agentId.charCodeAt(i)) % AGENT_ICONS.length;
  }
  return AGENT_ICONS[Math.abs(hash) % AGENT_ICONS.length];
}

function relativeTime(timestamp: string): string {
  const deltaMs = Date.now() - Date.parse(timestamp);
  if (Number.isNaN(deltaMs) || deltaMs < 0) return "";
  if (deltaMs < 1000) return "just now";
  if (deltaMs < 60_000) return `${Math.round(deltaMs / 1000)}s ago`;
  return `${Math.round(deltaMs / 60_000)}m ago`;
}

/** Real agent output preview recorded by the backend the instant that call completed. */
function summarize(detail: Record<string, unknown>): string {
  const preview = detail.output_preview;
  return typeof preview === "string" && preview.length > 0
    ? preview
    : "(agent call completed - no output preview recorded)";
}

function stepId(detail: Record<string, unknown>): string | null {
  return typeof detail.step_id === "string" ? detail.step_id : null;
}

function stepLabel(id: string | null): string | null {
  if (!id) return null;
  return MISSION_PHASES.find((phase) => phase.stepId === id)?.label ?? id;
}

function delegatedBy(detail: Record<string, unknown>): string | null {
  return typeof detail.delegated_by === "string" ? detail.delegated_by : null;
}

const MAX_FEED_ITEMS = 30;
const XP_PER_CALL = 10;
const XP_PER_LEVEL = 50;

interface TriageStats {
  xp: number;
  level: number;
  levelProgressPct: number;
  callCount: number;
}

function computeStats(callCount: number): TriageStats {
  const xp = callCount * XP_PER_CALL;
  const level = Math.floor(xp / XP_PER_LEVEL) + 1;
  const levelProgressPct = ((xp % XP_PER_LEVEL) / XP_PER_LEVEL) * 100;
  return { xp, level, levelProgressPct, callCount };
}

const PHASE_ICONS: Record<string, string> = {
  "analyze-requirements": "📋",
  "design-architecture": "🏗️",
  "build-solution": "🤖",
  "governance-review": "🔐",
  "deploy-solution": "🚀",
};

type FlowNodeStatus = "complete" | "active" | "pending";

/**
 * One glowing status node in the mission control flow - reuses the exact
 * same node/connector visual language (`genie-stage-node-*`,
 * `genie-stage-line-active`) as the AppShell sidebar's mission-flow, so the
 * control flow reads identically everywhere it appears instead of being its
 * own disconnected mini-widget.
 */
function FlowNode({ icon, status, label }: { icon: string; status: FlowNodeStatus; label: string }): JSX.Element {
  const nodeClass =
    status === "complete"
      ? "genie-stage-node-complete"
      : status === "active"
        ? "genie-stage-node-active"
        : "genie-stage-node-locked";
  const borderColor = status === "complete" ? "#3fa66a" : status === "active" ? "#d99a2b" : "#2a323d";
  const backgroundColor =
    status === "complete" ? "rgba(63, 166, 106, 0.15)" : status === "active" ? "rgba(217, 154, 43, 0.15)" : "#161c24";
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
        border: `2px solid ${borderColor}`,
        backgroundColor,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: 14,
      }}
    >
      {icon}
      {status === "complete" ? (
        <span
          style={{
            position: "absolute",
            bottom: -3,
            right: -3,
            width: 14,
            height: 14,
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
        backgroundColor: state === "complete" ? "#3fa66a" : state === "pending" ? "#232a33" : undefined,
        opacity: state === "pending" ? 0.6 : 1,
      }}
    />
  );
}

/**
 * Horizontal map of the mission's control flow - which stage has finished,
 * which is running right now, and which is still ahead - so the live feed
 * below reads as "here's what's happening within this stage" instead of
 * being the only place showing stage sequence at all.
 */
function ControlFlowMap({ completedStepIds, activeStepId }: { completedStepIds: Set<string>; activeStepId: string | null }): JSX.Element {
  return (
    <div style={{ display: "flex", alignItems: "center", width: "100%" }}>
      {MISSION_PHASES.map((phase, index) => {
        const status: FlowNodeStatus = completedStepIds.has(phase.stepId)
          ? "complete"
          : phase.stepId === activeStepId
            ? "active"
            : "pending";
        const isLast = index === MISSION_PHASES.length - 1;
        const connectorState: FlowNodeStatus = completedStepIds.has(phase.stepId)
          ? MISSION_PHASES[index + 1]?.stepId === activeStepId
            ? "active"
            : "complete"
          : "pending";
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

/**
 * Floating "triage mode" overlay: a concise, gamified live feed of real
 * agent calls made by the orchestrator, driven by the session's governance
 * event trail (`GET /sessions/{id}/governance/events`, category
 * `agent_execution`) rather than the batched `WorkflowRunResult`.
 *
 * Why this data source matters: `WorkflowRuntime.run_workflow` can execute
 * several ungated steps (e.g. build-solution -> governance-review) inside
 * one synchronous run/resume HTTP call, and the run's stored result is only
 * updated once that whole call returns - polling the run would make the
 * feed jump in batches, not calls. Each individual agent call, by contrast,
 * is recorded to the governance event repository the instant *that* call
 * completes (`WorkflowStepExecutor.execute_step`), so a concurrent poll
 * here observes every agent call in true orchestrator call order, even
 * while a later step in the same batch is still executing. `output_preview`
 * on each event is a truncated slice of that same agent's real output text
 * - never synthetic content.
 *
 * Every mission step is actually executed as: `genie-orchestrator` (the
 * step's own agent) calls exactly one `call_<agent>` delegation tool, which
 * runs the real specialist agent for that phase
 * (`app.agents.tools.orchestration_tools`). Both calls are recorded as
 * their own governance events with the same `step_id`, so this panel skips
 * the orchestrator's own top-level event (its output is always identical
 * to the specialist's - it just relays it verbatim) and shows only the
 * specialist call, labeled with which stage it belongs to and which agent
 * delegated it - that's the actual control flow of the mission.
 */
export function TriagePanel({ enabled }: { enabled: boolean }): JSX.Element | null {
  const { sessionId } = useSessionContext();

  const eventsFetcher = useCallback(
    () =>
      sessionId ? governanceApi.listEvents(sessionId) : Promise.reject(new Error("No active session")),
    [sessionId],
  );
  const { data: events } = useAsyncResource(eventsFetcher, [sessionId], {
    enabled: enabled && Boolean(sessionId),
    pollIntervalMs: 2000,
  });

  const allCalls = useMemo(
    () =>
      [...(events ?? [])]
        .filter((event): event is GovernanceEvent => event.category === "agent_execution")
        .sort((a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp)),
    [events],
  );

  // The orchestrator's own top-level call for a step is dropped from the
  // visible feed (see doc comment above) - it never carries information the
  // delegated specialist's own event doesn't already have.
  const agentCalls = useMemo(
    () => allCalls.filter((event) => event.detail.workflow_step !== true),
    [allCalls],
  );

  const completedStepIds = useMemo(() => {
    const ids = new Set<string>();
    for (const event of allCalls) {
      const id = stepId(event.detail);
      if (id) ids.add(id);
    }
    return ids;
  }, [allCalls]);
  const activeStepId = MISSION_PHASES.find((phase) => !completedStepIds.has(phase.stepId))?.stepId ?? null;

  const stats = useMemo(() => computeStats(agentCalls.length), [agentCalls]);
  const isLive =
    agentCalls.length > 0 && Date.now() - Date.parse(agentCalls[0].timestamp) < 10_000;

  if (!enabled) return null;

  return (
    <div
      className="genie-fade-in"
      role="complementary"
      aria-label="Agent triage traceability"
      style={{
        position: "fixed",
        top: 0,
        right: 0,
        bottom: 0,
        width: 340,
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
            🕹️ Agent Triage
          </Text>
          {isLive ? <span className="genie-live-dot" aria-label="Live" title="Live" /> : null}
        </div>
        <Text size={200} style={{ opacity: 0.7, display: "block" }}>
          Live feed of every real agent call, in orchestrator call order
        </Text>
      </div>

      <div
        className="genie-fade-in"
        style={{
          padding: "14px 16px",
          borderBottom: "1px solid #232a33",
          backgroundImage: "radial-gradient(circle at 0% 0%, rgba(47, 131, 224, 0.10), transparent 65%)",
        }}
      >
        <Text
          size={100}
          className="genie-stage-eyebrow"
          style={{ display: "block", opacity: 0.65, marginBottom: 10 }}
        >
          🧭 Mission Control Flow
        </Text>
        <ControlFlowMap completedStepIds={completedStepIds} activeStepId={activeStepId} />
        <Text size={200} style={{ display: "block", marginTop: 10, opacity: 0.85 }}>
          {activeStepId
            ? `⏳ Now: ${stepLabel(activeStepId)}`
            : completedStepIds.size >= MISSION_PHASES.length
              ? "✅ Mission complete"
              : "Awaiting mission start"}
        </Text>
      </div>

      <div style={{ padding: "12px 16px", borderBottom: "1px solid #232a33" }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
          <Text size={300} weight="semibold">
            Level {stats.level}
          </Text>
          <Text size={300} style={{ opacity: 0.8 }}>
            {stats.xp} XP
          </Text>
        </div>
        <div style={{ height: 6, borderRadius: 4, backgroundColor: "#232a33", overflow: "hidden" }}>
          <div
            className="genie-xp-bar"
            style={{ height: "100%", width: `${stats.levelProgressPct}%`, backgroundColor: "#2f83e0" }}
          />
        </div>
        <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
          <Badge shape="rounded" style={{ backgroundColor: "#3fa66a", color: "#0b0f14" }}>
            ✅ {stats.callCount} agent calls
          </Badge>
        </div>
      </div>

      <div style={{ overflowY: "auto", padding: "4px 12px 8px", flex: 1, minHeight: 0 }}>
        {agentCalls.length === 0 ? (
          <Text size={200} style={{ opacity: 0.7, padding: 8, display: "block" }}>
            No agent activity yet - start a workflow run to see live traceability.
          </Text>
        ) : (
          agentCalls.slice(0, MAX_FEED_ITEMS).map((event) => (
            <div
              key={event.id}
              className="genie-fade-in"
              style={{ display: "flex", gap: 8, padding: "8px 4px", borderBottom: "1px solid #1a2028" }}
            >
              <span style={{ fontSize: 18 }} aria-hidden="true">
                {iconForAgent(event.agent_id ?? "unknown-agent")}
              </span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 6 }}>
                  <Text
                    size={200}
                    weight="semibold"
                    style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                  >
                    {event.agent_id ?? "unknown-agent"}
                  </Text>
                  <Text size={200} style={{ color: "#3fa66a", whiteSpace: "nowrap" }}>
                    +{XP_PER_CALL} XP
                  </Text>
                </div>
                {delegatedBy(event.detail) ? (
                  <Text size={100} style={{ opacity: 0.6, display: "block" }}>
                    🧭 {delegatedBy(event.detail)} → {event.agent_id ?? "unknown-agent"}
                    {stepLabel(stepId(event.detail)) ? ` · ${stepLabel(stepId(event.detail))}` : ""}
                  </Text>
                ) : stepLabel(stepId(event.detail)) ? (
                  <Text size={100} style={{ opacity: 0.6, display: "block" }}>
                    step: {stepLabel(stepId(event.detail))}
                  </Text>
                ) : null}
                <Text size={200} style={{ opacity: 0.8, display: "block" }}>
                  {summarize(event.detail)}
                </Text>
                <Text size={100} style={{ opacity: 0.55 }}>
                  ✅ {relativeTime(event.timestamp)}
                </Text>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
