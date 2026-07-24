/** Mirrors backend/app/models/mission_control_snapshot.py 1:1 - the primary
 * UI contract for the Mission Control Dashboard. */
import type { ApprovalRequest } from "./governance";
import type { AgentHandoff, DecisionGraph } from "./collaboration";
import type { WorkflowStatus } from "./workflow";

export type TimelineEntryKind = "workflow_step" | "handoff" | "approval" | "collaboration";
export type GovernanceStatus = "compliant" | "attention_required";

export interface TimelineEntry {
  kind: TimelineEntryKind;
  label: string;
  agent_id: string | null;
  timestamp: string;
}

export interface MemoryUpdateSummary {
  key: string;
  classification: string;
  agent_id: string;
  version: number;
  timestamp: string;
}

export interface MissionControlSnapshot {
  session_id: string;
  workflow_run_id: string | null;
  workflow_status: WorkflowStatus | null;
  mission_progress: number;
  active_agents: string[];
  completed_agents: string[];
  blocked_agents: string[];
  current_workflow_step: string | null;
  timeline: TimelineEntry[];
  approvals: ApprovalRequest[];
  handoffs: AgentHandoff[];
  memory_updates: MemoryUpdateSummary[];
  decision_graph: DecisionGraph | null;
  governance_status: GovernanceStatus;
  business_value_score: number;
  risk_score: number;
  readiness_score: number;
}
