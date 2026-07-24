/** Mirrors backend/app/models/reanalysis_models.py 1:1. */

export type ReanalysisRequestType =
  | "challenge_recommendation"
  | "modify_priorities"
  | "request_alternative_architecture"
  | "lower_cost_redesign"
  | "higher_security_redesign"
  | "mvp_redesign"
  | "fabric_first_redesign";

export type ReanalysisStatus = "routed" | "failed";

export interface ReanalysisRequest {
  id: string;
  session_id: string;
  workflow_run_id: string;
  trace_id: string;
  requested_by: string;
  request_type: ReanalysisRequestType;
  target_recommendation_id: string | null;
  rationale: string;
  requested_at: string;
}

export interface ReanalysisResult {
  id: string;
  reanalysis_request_id: string;
  routed_to_agent_id: string | null;
  status: ReanalysisStatus;
  detail: string;
  created_at: string;
}
