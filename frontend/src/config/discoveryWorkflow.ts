/**
 * Configuration for the Requirement -> Architecture -> Build -> Deploy
 * mission workflow, kicked off from Upload.
 *
 * Mirrors config/workflows/registry.yaml's `solution-discovery-workflow` id
 * (backend source of truth). The user then reviews results directly on
 * Requirements/Architecture/Workshop/Governance/Outputs.
 */
export const DISCOVERY_WORKFLOW_ID: string =
  (import.meta.env.VITE_DISCOVERY_WORKFLOW_ID as string | undefined) ??
  "solution-discovery-workflow";
