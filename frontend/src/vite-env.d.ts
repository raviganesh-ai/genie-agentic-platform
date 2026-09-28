/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_GENIE_API_BASE_URL?: string;
  readonly VITE_REQUIREMENTS_POLL_MS?: string;
  readonly VITE_ARCHITECTURE_STUDIO_POLL_MS?: string;
  readonly VITE_GOVERNANCE_POLL_MS?: string;
  /** Polling interval for the left-nav mission flow's live stage status (AppShell). */
  readonly VITE_MISSION_FLOW_POLL_MS?: string;
  /** Id of the workflow "Start Prototyping" starts (config/workflows/registry.yaml). */
  readonly VITE_DISCOVERY_WORKFLOW_ID?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;

}
