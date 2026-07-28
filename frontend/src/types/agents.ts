/** Mirrors the subset of backend/app/agents/models.py's AgentDefinition
 * actually rendered by the UI (Agentic Workflow diagram on Architecture
 * Studio) - fields the frontend never displays are intentionally omitted. */
export interface AgentSummary {
  id: string;
  name: string;
  role: string;
  description: string;
  connected_agent_ids: string[] | null;
  enabled: boolean;
}
