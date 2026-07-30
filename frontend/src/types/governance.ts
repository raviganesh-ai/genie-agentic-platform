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
  | "human_checkpoint_confirmation"
  | "risk_accepted";

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

/** Aggregate governance health, derived client-side from governance events,
 * approvals, and the real GovernanceGateReport verdict (see
 * useGovernanceTrace's deriveComplianceState) - "compliant" is only ever
 * reported once the Governance Reviewer agent has actually returned a
 * "reviewed" gate report with an "approved" decision; before that (or
 * while nothing has happened yet) the state is "pending", never a
 * premature default of "compliant". */
export type GovernanceComplianceState =
  | "compliant"
  | "pending"
  | "warning"
  | "blocked"
  | "incomplete"
  | "failed";

/** Mirrors backend/app/models/governance_gate_report.py 1:1. The Governance
 * Reviewer's ("Peer Reviewer") consolidated 4-gate verdict for one workflow
 * run: security, test coverage, architecture, and code quality. */
export type GateStatus = "pass" | "fail";
export type GateName = "security" | "test_coverage" | "architecture" | "code_quality";
export type PeerReviewDecision = "approved" | "blocked";
export type GovernanceGateReportStatus = "pending" | "reviewed" | "undetermined";

export interface GovernanceFinding {
  id: string;
  gate: GateName;
  severity: "critical" | "high" | "medium" | "low";
  description: string;
  recommendation: string;
}

export interface GovernanceGateReport {
  status: GovernanceGateReportStatus;
  security_gate: GateStatus | null;
  test_coverage_gate: GateStatus | null;
  architecture_gate: GateStatus | null;
  code_quality_gate: GateStatus | null;
  findings: GovernanceFinding[];
  decision: PeerReviewDecision | null;
  assessed_by_agent_id: string | null;
}

/** Mirrors backend/app/models/governance_gate_report.py's AgentAssessment(s)
 * 1:1. Each specialist agent's own single-gate verdict (Security Assessment
 * Agent's security gate, Test Generation Agent's test-coverage gate), read
 * directly from that agent's own step output - available as soon as that
 * step completes, without waiting for the slower, consolidated
 * GovernanceGateReport verdict above. */
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

/** Mirrors backend/app/models/service_policy.py 1:1. The real, consolidated
 * policy that will govern a build once it is deployed - never fabricated:
 * every field is either copied from an already-loaded, already-enforced
 * policy document (config/policies/{approval,governance,memory}_policy.yaml)
 * together with each deployment checkpoint's actual per-session approval
 * status, or the Governance Reviewer agent's own real narrative describing
 * the access control it decided this build needs, read verbatim from its
 * governance-review step output. */
export type ServicePolicyStatus = "pending" | "ready";

export type DeploymentCheckpointRuntimeStatus = ApprovalRequestStatus | "not_reached";

export interface DeploymentCheckpointStatus {
  checkpoint_id: string;
  name: string;
  description: string;
  required: boolean;
  status: DeploymentCheckpointRuntimeStatus;
}

export interface GovernanceTrackingPolicy {
  track_registration: boolean;
  track_versions: boolean;
  track_lifecycle: boolean;
  track_executions: boolean;
  track_communication: boolean;
  track_memory_reads: boolean;
  track_memory_writes: boolean;
  track_tool_requests: boolean;
  track_policy_evaluations: boolean;
  track_denied_access: boolean;
}

export interface DecisionLineagePolicy {
  require_evidence_references: boolean;
}

export interface SessionReplayPolicy {
  enabled: boolean;
}

export interface PersonalAgentMemoryPolicy {
  accessible_by: string;
}

export interface SharedCollaborationMemoryPolicy {
  accessible_by: string;
  emits_governance_events: boolean;
  require_approval_for_overwrite: boolean;
}

export interface EnterpriseKnowledgeMemoryPolicy {
  accessible_by: string;
  requires_approval_to_promote: boolean;
}

export interface MemoryAccessPolicy {
  personal_agent_memory: PersonalAgentMemoryPolicy;
  shared_collaboration_memory: SharedCollaborationMemoryPolicy;
  enterprise_knowledge_memory: EnterpriseKnowledgeMemoryPolicy;
}

export interface ServicePolicy {
  status: ServicePolicyStatus;
  deployment_checkpoints: DeploymentCheckpointStatus[];
  governance_tracking: GovernanceTrackingPolicy;
  decision_lineage: DecisionLineagePolicy;
  session_replay: SessionReplayPolicy;
  memory_access_policy: MemoryAccessPolicy;
  access_control_summary: string;
  assessed_by_agent_id: string | null;
}
