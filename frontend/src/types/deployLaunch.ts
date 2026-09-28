/** Mirrors backend/app/deploy_launch/models.py 1:1. */

export type DeploymentStepId =
  | "generate-access-policy"
  | "provision-foundry-agents"
  | "deploy-backend-service"
  | "sync-frontend-integration"
  | "deploy-frontend-app"
  | "generate-test-suite"
  | "execute-test-suite"
  | "run-security-scan"
  | "launch-mission";

/** Ordered pipeline - mirrors `DEPLOYMENT_STEP_ORDER`/`DEPLOYMENT_STEP_NAMES`. */
export const DEPLOYMENT_STEP_ORDER: DeploymentStepId[] = [
  "generate-access-policy",
  "provision-foundry-agents",
  "deploy-backend-service",
  "sync-frontend-integration",
  "deploy-frontend-app",
  "generate-test-suite",
  "execute-test-suite",
  "launch-mission",
];

export const DEPLOYMENT_STEP_NAMES: Record<DeploymentStepId, string> = {
  "generate-access-policy": "Generate Access Policy & Least Access",
  "provision-foundry-agents": "Deploy Agents to Foundry",
  "deploy-backend-service": "Deploy Backend Service",
  "sync-frontend-integration": "Update Frontend Integrations",
  "deploy-frontend-app": "Deploy Frontend",
  "generate-test-suite": "Generate Requirement Acceptance Tests",
  "execute-test-suite": "Requirement Fidelity Gate",
  "run-security-scan": "Security Scan (Backend & Frontend)",
  "launch-mission": "Launch",
};

export type DeploymentStepStatus = "pending" | "running" | "completed" | "failed" | "skipped";
export type DeploymentPipelineStatus = "pending" | "running" | "completed" | "failed";

export interface AgentAccessPolicy {
  agent_id: string;
  role: string;
  allowed_tools: string[];
  memory_access: string[];
}

export interface AccessPolicyDocument {
  generated_at: string;
  agents: AgentAccessPolicy[];
}

export interface DeploymentStepResult {
  step_id: DeploymentStepId;
  name: string;
  status: DeploymentStepStatus;
  detail: string;
  error: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface ProvisionedAgentStatus {
  agent_name: string;
  status: DeploymentStepStatus;
  foundry_agent_name: string | null;
}

export type RequirementFidelityStatus = "pending" | "testing" | "repairing" | "passed" | "failed";
export type RequirementEvidenceStatus = "pending" | "covered" | "passed" | "failed" | "missing";

export interface RequirementFidelityItem {
  requirement_id: string;
  statement: string;
  status: RequirementEvidenceStatus;
  test_names: string[];
  evidence: string;
}

export interface RequirementFidelityReport {
  status: RequirementFidelityStatus;
  requirements: RequirementFidelityItem[];
  total_requirements: number;
  covered_requirements: number;
  passed_requirements: number;
  coverage_percent: number;
  pass_percent: number;
  repair_attempts: number;
  max_repair_attempts: number;
  gaps: string[];
  execution_summary: string;
}

export interface DeploymentPipelineRun {
  id: string;
  session_id: string;
  workflow_run_id: string;
  status: DeploymentPipelineStatus;
  steps: DeploymentStepResult[];
  access_policy: AccessPolicyDocument | null;
  provisioned_agents: ProvisionedAgentStatus[];
  backend_url: string | null;
  frontend_url: string | null;
  launch_url: string | null;
  test_summary: string | null;
  fidelity_report: RequirementFidelityReport | null;
  security_findings_count: number | null;
  created_at: string;
  updated_at: string;
}
