/**
 * Configuration for the Requirement -> Architecture -> Build -> Peer Review
 * mission workflow, kicked off from Upload.
 *
 * Mirrors config/workflows/registry.yaml's `solution-discovery-workflow` id
 * (backend source of truth). The user then reviews results directly on
 * Requirements/Architecture/Workshop/Peer Review/Outputs (Deploy & Launch,
 * a separate real pipeline, runs after this workflow completes).
 */
export const DISCOVERY_WORKFLOW_ID: string =
  (import.meta.env.VITE_DISCOVERY_WORKFLOW_ID as string | undefined) ??
  "solution-discovery-workflow";

/**
 * Ordered mission phases for `solution-discovery-workflow`
 * (config/workflows/registry.yaml `steps[].id`/`description`). Shared by
 * every UI surface that needs to render this mission's stage sequence
 * (Upload's progress bar, the Triage panel's control-flow map) so there is
 * exactly one place that mirrors the backend registry, instead of each
 * surface hardcoding its own copy that could drift out of sync.
 */
export const MISSION_PHASES: { stepId: string; label: string }[] = [
  { stepId: "analyze-requirements", label: "Analyzing requirements" },
  { stepId: "design-architecture", label: "Designing architecture" },
  { stepId: "build-solution", label: "Building UI & agent workflow" },
  { stepId: "peer-review", label: "Peer review" },
];
