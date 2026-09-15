/**
 * Configuration for the Requirement -> Architecture -> Build mission
 * workflow, kicked off from Upload.
 *
 * Mirrors config/workflows/registry.yaml's `solution-discovery-workflow` id
 * (backend source of truth). The user then reviews results directly on
 * Requirements/Architecture/Workshop/Outputs. Deploy & Launch (a separate
 * real pipeline) runs after this workflow completes, deploying the
 * actually-built prototype and then running an informational-only
 * Microsoft Security Copilot scan and Azure FinOps cost report against it.
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

// Deploy & Launch's own eight steps (app.deploy_launch.pipeline_service,
// DEPLOYMENT_STEP_ORDER/DEPLOYMENT_STEP_NAMES) are a separate real pipeline
// that runs AFTER this discovery workflow, but it publishes its own live
// step_started/step_completed/step_failed events onto the SAME session-wide
// WorkflowEventBus/SSE stream (see pipeline_service.py's `_publish`, tagged
// with `agent_id="deploy-launch-pipeline"`) - so the Triage panel's Mission
// Trace can show a genuinely end-to-end trace (Requirements through Launch)
// by treating these as more mission phases, not just the 3 discovery ones.
// None of these require a separate human "proceed" gate - Deploy & Launch's
// only gate is the single Start button.
const DEPLOYMENT_PIPELINE_PHASES: MissionPhase[] = [
  { stepId: "generate-access-policy", label: "Generate Access Policy & Least Access" },
  { stepId: "provision-foundry-agents", label: "Deploy Agents to Foundry" },
  { stepId: "deploy-backend-service", label: "Deploy Backend Service" },
  { stepId: "sync-frontend-integration", label: "Update Frontend Integrations" },
  { stepId: "deploy-frontend-app", label: "Deploy Frontend" },
  { stepId: "security-copilot-scan", label: "Microsoft Security Copilot Scan" },
  { stepId: "finops-cost-report", label: "Azure FinOps Cost Report" },
  { stepId: "launch-mission", label: "Launch" },
].map(({ stepId, label }) => ({
  stepId,
  label,
  specialistAgentId: "deploy-launch-pipeline",
  specialistLabel: "Deploy & Launch Pipeline",
  requiresProceed: false,
}));

/** Full end-to-end mission trace: discovery's 3 phases followed by Deploy &
 * Launch's 8 real pipeline steps - what the Triage panel's Mission Trace
 * renders, so it never appears to "end" right after Build. */
export const END_TO_END_MISSION_PHASES: MissionPhase[] = [...MISSION_PHASES, ...DEPLOYMENT_PIPELINE_PHASES];
