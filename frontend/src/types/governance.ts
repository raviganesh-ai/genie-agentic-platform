/** Mirrors backend/app/models/{governance_event,approval_models,recommendation_lineage}.py 1:1. */

export type GovernanceEventCategory =
  | "agent_registration"
  | "agent_version"
  | "agent_lifecycle"
  | "agent_execution"
  | "agent_communication"
  | "memory_read"
  | "memory_write"
  | "tool_request"
  | "policy_evaluation"
  | "access_denied"
  | "human_checkpoint_confirmation";

export interface GovernanceEvent {
  id: string;
  category: GovernanceEventCategory;
  session_id: string;
  trace_id: string;
  agent_id: string | null;
  timestamp: string;
  detail: Record<string, unknown>;
}

export type ApprovalRequestStatus = "pending" | "approved" | "rejected" | "expired";
export type ApprovalDecisionOutcome = "approved" | "rejected";
export type ApprovalAuditEvent = "requested" | "approved" | "rejected" | "expired";

export interface ApprovalCheckpoint {
  id: string;
  name: string;
  description: string;
  required: boolean;
}

export interface ApprovalRequest {
  id: string;
  checkpoint_id: string;
  session_id: string;
  trace_id: string;
  requested_by_agent_id: string;
  subject_type: string;
  subject_id: string;
  status: ApprovalRequestStatus;
  requested_at: string;
  expires_at: string | null;
}

export interface ApprovalDecision {
  id: string;
  request_id: string;
  decision: ApprovalDecisionOutcome;
  decided_by: string;
  decided_at: string;
  rationale: string;
}

export interface ApprovalAuditRecord {
  id: string;
  request_id: string;
  session_id: string;
  trace_id: string;
  event: ApprovalAuditEvent;
  timestamp: string;
  actor: string;
  detail: string;
}

export interface RecommendationLineage {
  id: string;
  session_id: string;
  trace_id: string;
  recommendation_id: string;
  recommendation_type: string;
  produced_by_agent_id: string;
  produced_by_agent_version: string;
  evidence_references: string[];
  memory_references: string[];
  approval_ids: string[];
  confidence_score: number;
  timestamp: string;
}

/** Aggregate governance health, derived client-side from governance events +
 * approvals already returned by the backend (see useGovernanceTrace). */
export type GovernanceComplianceState =
  | "compliant"
  | "warning"
  | "blocked"
  | "incomplete"
  | "failed";
