/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_GENIE_API_BASE_URL?: string;
  readonly VITE_MISSION_CONTROL_POLL_MS?: string;
  readonly VITE_AGENT_ARENA_POLL_MS?: string;
  readonly VITE_COLLABORATION_GRAPH_POLL_MS?: string;
  readonly VITE_REQUIREMENTS_POLL_MS?: string;
  readonly VITE_ARCHITECTURE_STUDIO_POLL_MS?: string;
  readonly VITE_GOVERNANCE_POLL_MS?: string;
  readonly VITE_REPLAY_POLL_MS?: string;
  readonly VITE_FINAL_OUTPUT_POLL_MS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
