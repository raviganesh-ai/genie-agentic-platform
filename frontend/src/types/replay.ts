/** Mirrors backend/app/api/replay.py's SessionReplayResponse/
 * RecommendationTraceResponse 1:1. */
import type {
  ApprovalAuditRecord,
  ApprovalDecision,
  ApprovalRequest,
  GovernanceEvent,
  RecommendationLineage,
} from "./governance";
import type { DecisionGraph } from "./collaboration";

export interface SessionReplayResponse {
  session_id: string;
  governance_events: GovernanceEvent[];
  recommendation_lineage: RecommendationLineage[];
  approval_requests: ApprovalRequest[];
  approval_decisions: ApprovalDecision[];
  approval_audit_trail: ApprovalAuditRecord[];
  decision_graph: DecisionGraph | null;
}

export interface RecommendationTraceResponse {
  lineage: RecommendationLineage;
  approval_requests: ApprovalRequest[];
  approval_decisions: ApprovalDecision[];
  governance_events: GovernanceEvent[];
}

/** A single chronological entry in the Replay Center's step-by-step timeline,
 * derived client-side by merging governance events + approval audit trail +
 * decision graph evolution (see src/services/adapters/replayTimeline.ts). */
export interface ReplayTimelineEntry {
  id: string;
  timestamp: string;
  kind: "governance" | "approval" | "lineage";
  label: string;
  agentId: string | null;
}
