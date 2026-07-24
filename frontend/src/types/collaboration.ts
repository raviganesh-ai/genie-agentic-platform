/** Mirrors backend/app/models/{handoff_models,collaboration_models,decision_graph}.py 1:1. */

export interface AgentHandoff {
  id: string;
  source_agent_id: string;
  target_agent_id: string;
  session_id: string;
  workflow_run_id: string;
  trace_id: string;
  reason: string;
  evidence_references: string[];
  timestamp: string;
}

export type CollaborationType =
  | "agent_to_agent"
  | "shared_memory"
  | "recommendation_dependency"
  | "approval_dependency";

export interface CollaborationEvent {
  id: string;
  collaboration_type: CollaborationType;
  session_id: string;
  workflow_run_id: string;
  trace_id: string;
  source_agent_id: string;
  target_agent_id: string | null;
  memory_reference: string | null;
  recommendation_id: string | null;
  approval_id: string | null;
  detail: string;
  timestamp: string;
}

export type DecisionNodeType =
  | "agent"
  | "recommendation"
  | "approval"
  | "memory_record"
  | "evidence";

export type DecisionEdgeType =
  | "agent_to_agent"
  | "recommendation_dependency"
  | "approval_dependency"
  | "evidence_dependency"
  | "memory_dependency";

export interface DecisionNode {
  id: string;
  node_type: DecisionNodeType;
  label: string;
  session_id: string;
  metadata: Record<string, unknown>;
}

export interface DecisionEdge {
  id: string;
  edge_type: DecisionEdgeType;
  source_id: string;
  target_id: string;
  session_id: string;
  metadata: Record<string, unknown>;
}

export interface DecisionGraph {
  session_id: string;
  nodes: DecisionNode[];
  edges: DecisionEdge[];
}
