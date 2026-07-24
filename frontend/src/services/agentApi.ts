import { apiFetch } from "./httpClient";
import type { AgentDefinition } from "@/types/agent";

export const agentApi = {
  list(): Promise<AgentDefinition[]> {
    return apiFetch<AgentDefinition[]>("/agents");
  },
  get(agentId: string): Promise<AgentDefinition> {
    return apiFetch<AgentDefinition>(`/agents/${agentId}`);
  },
};
