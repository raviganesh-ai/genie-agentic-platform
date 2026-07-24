/** Mirrors backend/app/memory/memory_models.py::SharedMemoryRecord + MemoryLineage 1:1
 * (only Shared Collaboration Memory is exposed to the UI - see
 * backend/app/api/memory.py's docstring for why Personal/Enterprise tiers
 * are intentionally not exposed here). */

export type ApprovalStatus = "not_required" | "pending" | "approved" | "rejected";

export type SharedMemoryClassification =
  | "goal"
  | "requirement"
  | "constraint"
  | "risk"
  | "assumption"
  | "approval"
  | "architecture_finding"
  | "roadmap_artifact";

export interface MemoryLineage {
  session_id: string;
  trace_id: string;
  agent_id: string;
  agent_version: string;
  timestamp: string;
  evidence_references: string[];
  confidence_score: number;
  approval_status: ApprovalStatus;
}

export interface SharedMemoryRecord {
  id: string;
  session_id: string;
  classification: SharedMemoryClassification;
  content: Record<string, unknown>;
  lineage: MemoryLineage;
  version: number;
}
