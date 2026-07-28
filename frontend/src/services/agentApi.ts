import { apiFetch } from "./httpClient";
import type { AgentSummary } from "@/types/agents";

/**
 * Thin client for the backend's real, read-only `/agents` router (backend/
 * app/api/agents.py), itself a wrapper over the externally configured
 * AgentRegistry (config/agents/registry.yaml). Used to render the Agentic
 * Workflow diagram (Orchestrator + its delegated specialist agents) with
 * each agent's real name/description/connections - never hardcoded.
 */
export const agentApi = {
  list(): Promise<AgentSummary[]> {
    return apiFetch<AgentSummary[]>("/agents");
  },
};
