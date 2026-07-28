import { useCallback, useMemo } from "react";
import { Badge, Text } from "@fluentui/react-components";
import { useSessionContext } from "@/state/SessionContext";
import { useAsyncResource } from "@/hooks/useAsyncResource";
import { governanceApi } from "@/services/governanceApi";
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

function stepLabel(detail: Record<string, unknown>): string | null {
  return typeof detail.step_id === "string" ? detail.step_id : null;
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

  const agentCalls = useMemo(
    () =>
      [...(events ?? [])]
        .filter((event): event is GovernanceEvent => event.category === "agent_execution")
        .sort((a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp)),
    [events],
  );
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
                {stepLabel(event.detail) ? (
                  <Text size={100} style={{ opacity: 0.6, display: "block" }}>
                    step: {stepLabel(event.detail)}
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
