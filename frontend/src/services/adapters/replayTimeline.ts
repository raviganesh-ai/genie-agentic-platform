import type { SessionReplayResponse, ReplayTimelineEntry } from "@/types/replay";

/**
 * Merges the three independently-timestamped arrays in a
 * `SessionReplayResponse` into one chronological, replay-ready timeline.
 * Purely a display-ordering transform of real backend data - no values are
 * invented.
 */
export function buildReplayTimeline(replay: SessionReplayResponse): ReplayTimelineEntry[] {
  const entries: ReplayTimelineEntry[] = [];

  for (const event of replay.governance_events) {
    entries.push({
      id: `governance:${event.id}`,
      timestamp: event.timestamp,
      kind: "governance",
      label: event.category.replace(/_/g, " "),
      agentId: event.agent_id,
    });
  }

  for (const record of replay.approval_audit_trail) {
    entries.push({
      id: `approval:${record.id}`,
      timestamp: record.timestamp,
      kind: "approval",
      label: `${record.event} by ${record.actor}`,
      agentId: null,
    });
  }

  for (const lineage of replay.recommendation_lineage) {
    entries.push({
      id: `lineage:${lineage.id}`,
      timestamp: lineage.timestamp,
      kind: "lineage",
      label: `Recommendation ${lineage.recommendation_type} produced`,
      agentId: lineage.produced_by_agent_id,
    });
  }

  return entries.sort((a, b) => a.timestamp.localeCompare(b.timestamp));
}
