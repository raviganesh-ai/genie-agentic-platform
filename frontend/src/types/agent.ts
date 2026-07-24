/** Mirrors backend/app/agents/models.py::AgentDefinition 1:1.
 *
 * Never carries foundry_agent_id/model_deployment_ref into any Foundry call -
 * the frontend must never call Azure AI Foundry directly. These fields are
 * only ever rendered as informational/telemetry text when the backend
 * chooses to include them.
 */

export type MemoryTier = "personal" | "shared" | "enterprise";

export interface AgentDefinition {
  id: string;
  name: string;
  role: string;
  description: string;
  capabilities: string[];
  allowed_tools: string[];
  memory_access: MemoryTier[];
  model_deployment_ref: string | null;
  foundry_agent_id: string | null;
  enabled: boolean;
  version: string;
}

/**
 * Agent Arena UI status derived client-side from workflow step results /
 * mission control active/blocked lists / approval state - the backend has
 * no single "AgentStatus" enum field, so this is an explicit, documented
 * frontend adapter (see src/services/adapters/agentStatus.ts), not an
 * invented backend contract.
 */
export type AgentActivityStatus =
  | "idle"
  | "analyzing"
  | "collaborating"
  | "waiting_for_approval"
  | "generating_output"
  | "completed"
  | "blocked"
  | "failed";
