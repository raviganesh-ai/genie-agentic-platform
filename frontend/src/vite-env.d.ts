/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_GENIE_API_BASE_URL?: string;
  /** Microsoft Entra ID application (client) id used for the automatic sign-in flow. */
  readonly VITE_ENTRA_CLIENT_ID?: string;
  /** Microsoft Entra ID tenant id used for the automatic sign-in flow. */
  readonly VITE_ENTRA_TENANT_ID?: string;
  /** Full scope URI (e.g. api://<client-id>/access_as_user) requested for the Genie backend API. */
  readonly VITE_ENTRA_API_SCOPE?: string;
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
