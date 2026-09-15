/** Mirrors backend/app/deploy_launch/models.py 1:1. */

export type DeploymentStepId =
  | "generate-access-policy"
  | "provision-foundry-agents"
  | "deploy-backend-service"
  | "sync-frontend-integration"
  | "deploy-frontend-app"
  | "security-copilot-scan"
  | "finops-cost-report"
  | "launch-mission";

/** Ordered pipeline - mirrors `DEPLOYMENT_STEP_ORDER`/`DEPLOYMENT_STEP_NAMES`. */
export const DEPLOYMENT_STEP_ORDER: DeploymentStepId[] = [
  "generate-access-policy",
  "provision-foundry-agents",
  "deploy-backend-service",
  "sync-frontend-integration",
  "deploy-frontend-app",
  "security-copilot-scan",
  "finops-cost-report",
  "launch-mission",
];

export const DEPLOYMENT_STEP_NAMES: Record<DeploymentStepId, string> = {
  "generate-access-policy": "Generate Access Policy & Least Access",
  "provision-foundry-agents": "Deploy Agents to Foundry",
  "deploy-backend-service": "Deploy Backend Service",
  "sync-frontend-integration": "Update Frontend Integrations",
  "deploy-frontend-app": "Deploy Frontend",
  "security-copilot-scan": "Microsoft Defender & Security Copilot Scan",
  "finops-cost-report": "Azure FinOps Cost Report",
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

export type SecurityFindingSeverity = "informational" | "low" | "medium" | "high" | "critical";
export type SecurityFindingSource = "defender-for-cloud" | "security-copilot";

export interface SecurityCopilotFinding {
  source: SecurityFindingSource;
  severity: SecurityFindingSeverity;
  title: string;
  description: string;
  resource: string | null;
}

export interface SecurityCopilotScanReport {
  available: boolean;
  summary: string;
  findings: SecurityCopilotFinding[];
  reference_url: string | null;
  scanned_at: string | null;
}

export interface FinOpsCostLineItem {
  resource_type: string;
  cost: number;
}

export type FinOpsDataSource = "azure-cost-management" | "finops-hub";

export interface FinOpsCostReport {
  available: boolean;
  summary: string;
  data_source: FinOpsDataSource;
  total_cost: number | null;
  currency: string | null;
  line_items: FinOpsCostLineItem[];
  period_start: string | null;
  period_end: string | null;
  reported_at: string | null;
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
  security_scan_report: SecurityCopilotScanReport | null;
  cost_report: FinOpsCostReport | null;
  created_at: string;
  updated_at: string;
}

