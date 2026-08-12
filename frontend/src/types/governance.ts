/** Mirrors backend/app/models/{governance_event,approval_models,recommendation_lineage,governance_gate_report}.py 1:1. */

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

/** Aggregate governance health, derived client-side from governance events
 * and approvals (see useGovernanceTrace's deriveComplianceState). */
export type GovernanceComplianceState =
  | "compliant"
  | "pending"
  | "warning"
  | "blocked"
  | "incomplete"
  | "failed";

/** Mirrors backend/app/models/governance_gate_report.py 1:1. */
export type GateStatus = "pass" | "fail";
export type GateName = "requirements" | "security" | "test_coverage" | "architecture" | "code_quality";

export interface GovernanceFinding {
  id: string;
  gate: GateName;
  severity: "critical" | "high" | "medium" | "low";
  description: string;
  recommendation: string;
}

/** Mirrors backend/app/models/governance_gate_report.py's AgentAssessment(s)
 * 1:1. Each specialist agent's own single-gate verdict (Security Assessment
 * Agent's security gate, Test Generation Agent's test-coverage gate), read
 * directly from that agent's own step output - available as soon as that
 * step completes. */
export type AgentAssessmentStatus = "pending" | "reviewed" | "undetermined";

export interface AgentAssessment {
  status: AgentAssessmentStatus;
  gate: GateStatus | null;
  summary: string;
  findings: GovernanceFinding[];
  tests_generated: number;
  assessed_by_agent_id: string | null;
}

export interface AgentAssessmentsReport {
  security_assessment: AgentAssessment;
  test_generation: AgentAssessment;
}
