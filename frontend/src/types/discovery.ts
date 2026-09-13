export type DiscoveryStatus =
  | "created"
  | "analyzing_personas"
  | "awaiting_persona_selection"
  | "analyzing_persona"
  | "awaiting_qa_mode"
  | "questioning"
  | "ready_for_solutions"
  | "generating_solutions"
  | "awaiting_solution_selection"
  | "ready_to_prototype"
  | "build_started"
  | "failed";

export type DiscoveryQaMode = "batch" | "interactive";
export type DiscoveryQuestionStatus =
  | "pending"
  | "answered"
  | "recommendation_offered"
  | "recommended"
  | "recommendation_declined";

export interface PersonaProfile {
  id: string;
  name: string;
  role_or_context?: string | null;
  description?: string | null;
  pain_points: string[];
  evidence_references: string[];
  confidence_score?: number | null;
}

export interface GapAnalysis {
  known_facts: string[];
  risks: string[];
  contradictions: string[];
  information_gaps: string[];
  assumptions: string[];
  evidence_references: string[];
  confidence_score: number;
}

export interface DiscoveryInsightSection {
  title: string;
  summary: string;
  evidence_references: string[];
}

export interface DiscoveryQuestion {
  id: string;
  text: string;
  category: string;
  suggested_answers: string[];
  status: DiscoveryQuestionStatus;
  answer: string | null;
  recommendation: string | null;
  evidence_references: string[];
}

export interface ArchitectureNode {
  id: string;
  service_name: string;
  azure_icon_key: string;
  purpose: string;
  x: number;
  y: number;
}

export interface ArchitectureEdge {
  id: string;
  source: string;
  target: string;
  label: string;
}

export interface PricingQuery {
  service_name: string;
  retail_service_name?: string | null;
  product_name?: string | null;
  arm_region_name: string;
  sku_name: string | null;
  meter_name?: string | null;
  unit_of_measure?: string | null;
  units_per_month: number;
  assumption: string;
}

export interface CostEstimate {
  currency_code: string;
  region: string;
  monthly_amount: number | null;
  annual_amount: number | null;
  coverage: "complete" | "partial" | "unavailable";
  assumptions: string[];
  source_urls: string[];
  retrieved_at: string | null;
}

export interface ProposedSolution {
  id: string;
  name: string;
  summary: string;
  requirements_text: string;
  architecture_text: string;
  architecture_nodes: ArchitectureNode[];
  architecture_edges: ArchitectureEdge[];
  pros: string[];
  cons: string[];
  ai_feasibility: "recommended" | "feasible_with_tradeoffs" | "not_feasible";
  ai_feasibility_rationale: string;
  evidence_references: string[];
  pricing_queries: PricingQuery[];
  cost_estimate: CostEstimate;
}

export interface DiscoveryCase {
  id: string;
  session_id: string;
  owner_user_id: string;
  save_enabled: boolean;
  model_deployment_ref: string | null;
  status: DiscoveryStatus;
  source_upload_ids: string[];
  analyzed_upload_ids: string[];
  analysis_revision: number;
  personas: PersonaProfile[];
  selected_persona_id: string | null;
  selected_persona_ids: string[];
  deep_dive_findings: string[];
  insight_sections: DiscoveryInsightSection[];
  gap_summary: string;
  assumption_summary: string;
  gap_analysis: GapAnalysis | null;
  qa_mode: DiscoveryQaMode | null;
  questions: DiscoveryQuestion[];
  proposed_solutions: ProposedSolution[];
  selected_solution_id: string | null;
  build_workflow_run_id: string | null;
  last_error: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}