/**
 * Configuration for the Requirement -> Architecture -> Build mission
 * workflow, kicked off from Upload.
 *
 * Mirrors config/workflows/registry.yaml's `solution-discovery-workflow` id
 * (backend source of truth). The user then reviews results directly on
 * Requirements/Architecture/Workshop/Outputs. Deploy & Launch (a separate
 * real pipeline) runs after this workflow completes, and generates + runs
 * the test suite for real there, against the actually-deployed build.
 */
export const DISCOVERY_WORKFLOW_ID: string =
  (import.meta.env.VITE_DISCOVERY_WORKFLOW_ID as string | undefined) ??
  "solution-discovery-workflow";

export interface MissionPhase {
  stepId: string;
  label: string;
  /** The real specialist agent id this phase's step delegates to (see
   * `allowed_tool_names`/`resolve_delegate_agent_id` - every step's own
   * configured `agent_id` is `genie-orchestrator`, which just relays this
   * specialist's real output verbatim). Used as the display label BEFORE
   * any live event has arrived for this phase yet. */
  specialistAgentId: string;
  specialistLabel: string;
  /** Mirrors `WorkflowStep.requires_human_proceed` - true means this
   * step's wave is gated: it will not start until a human explicitly
   * proceeds from this phase's own page (Requirements/Architecture), even
   * though the PRIOR phase already finished. */
  requiresProceed: boolean;
}

/**
 * Ordered mission phases for `solution-discovery-workflow`
 * (config/workflows/registry.yaml `steps[].id`/`description`). Shared by
 * every UI surface that needs to render this mission's stage sequence
 * (Upload's progress bar, the Triage panel's mission trace) so there is
 * exactly one place that mirrors the backend registry, instead of each
 * surface hardcoding its own copy that could drift out of sync.
 *
 * IMPORTANT: keep this list's `stepId`s and `requiresProceed` flags in
 * sync with `config/workflows/registry.yaml`'s `solution-discovery-workflow`
 * steps - this list previously drifted (retained a since-removed
 * `security-assessment` step), which made the Triage panel's progress map
 * get permanently stuck showing that phase as "in progress" forever.
 */
export const MISSION_PHASES: MissionPhase[] = [
  {
    stepId: "analyze-requirements",
    label: "Requirements",
    specialistAgentId: "requirements-analyst",
    specialistLabel: "Requirements Analyst",
    requiresProceed: false,
  },
  {
    stepId: "design-architecture",
    label: "Architecture",
    specialistAgentId: "architecture-designer",
    specialistLabel: "Architecture Designer",
    requiresProceed: true,
  },
  {
    stepId: "build-solution",
    label: "Build (UI & Agent Workflow)",
    specialistAgentId: "build-agent",
    specialistLabel: "Build Agent",
    requiresProceed: true,
  },
];
