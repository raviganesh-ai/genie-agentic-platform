/** Mirrors backend/app/models/{workflow_state,workflow_models}.py 1:1. */

export type WorkflowStatus =
  | "pending"
  | "running"
  | "waiting_for_agent"
  | "waiting_for_approval"
  | "blocked"
  | "failed"
  | "completed";

export interface WorkflowState {
  workflow_run_id: string;
  workflow_id: string;
  session_id: string;
  status: WorkflowStatus;
  current_wave_index: number;
  active_step_ids: string[];
  completed_step_ids: string[];
  failed_step_ids: string[];
  detail: string;
  updated_at: string;
}

export type WorkflowStepStatus = "completed" | "failed";

export interface WorkflowStepInput {
  step_id: string;
  variables: Record<string, string>;
  prompt_id?: string | null;
}

export interface WorkflowStepResult {
  step_id: string;
  agent_id: string;
  status: WorkflowStepStatus;
  output_text: string | null;
  error: string | null;
  started_at: string;
  completed_at: string;
}

export interface WorkflowRunResult {
  workflow_run_id: string;
  workflow_id: string;
  session_id: string;
  status: WorkflowStatus;
  waves: string[][];
  step_results: WorkflowStepResult[];
  detail: string;
}

export type DeliverableType =
  | "requirements_package"
  | "architecture_package"
  | "roadmap_package"
  | "executive_summary_package"
  | "final_output_package";

export interface DeliverablePackage {
  id: string;
  deliverable_type: DeliverableType;
  session_id: string;
  workflow_run_id: string;
  generated_at: string;
  sections: Record<string, string>;
}
