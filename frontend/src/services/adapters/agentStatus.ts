import type { AgentActivityStatus } from "@/types/agent";
import type { ApprovalRequest } from "@/types/governance";
import type { WorkflowRunResult, WorkflowStepResult } from "@/types/workflow";

/**
 * Frontend adapter: derives a single `AgentActivityStatus` for one agent
 * from real, already-returned backend state - never invented. The backend
 * has no single "agent status" enum field, so this composes:
 *  - MissionControlSnapshot.active_agents/completed_agents/blocked_agents
 *  - the latest WorkflowRunResult.step_results for that agent
 *  - any pending ApprovalRequest requested by that agent
 */
export function deriveAgentActivityStatus(params: {
  agentId: string;
  activeAgents: string[];
  completedAgents: string[];
  blockedAgents: string[];
  latestRun: WorkflowRunResult | null;
  pendingApprovals: ApprovalRequest[];
}): AgentActivityStatus {
  const { agentId, activeAgents, completedAgents, blockedAgents, latestRun, pendingApprovals } =
    params;

  if (blockedAgents.includes(agentId)) return "blocked";

  const stepResults = latestRun?.step_results.filter((r) => r.agent_id === agentId) ?? [];
  const hasFailed = stepResults.some((r: WorkflowStepResult) => r.status === "failed");
  if (hasFailed) return "failed";

  const hasPendingApproval = pendingApprovals.some(
    (approval) => approval.requested_by_agent_id === agentId && approval.status === "pending",
  );
  if (hasPendingApproval) return "waiting_for_approval";

  if (completedAgents.includes(agentId) && stepResults.every((r) => r.status === "completed")) {
    return "completed";
  }

  if (activeAgents.includes(agentId)) {
    if (latestRun?.status === "waiting_for_approval") return "waiting_for_approval";
    return stepResults.length > 0 ? "generating_output" : "analyzing";
  }

  return "idle";
}
