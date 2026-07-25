/**
 * Configuration for the "Generate Prototype" auto-orchestration wizard.
 *
 * The workflow id and the step->stage mapping below mirror
 * config/workflows/registry.yaml's `solution-discovery-workflow` (backend
 * source of truth). If that workflow's step ids ever change, this mapping
 * must be updated to match - it is not derived dynamically because the
 * frontend never has direct access to the workflow registry, only to
 * MissionControlSnapshot's `current_workflow_step` id.
 */
import type { ApprovalRequest } from "@/types/governance";
import type { WorkflowStatus } from "@/types/workflow";

export const DISCOVERY_WORKFLOW_ID: string =
  (import.meta.env.VITE_DISCOVERY_WORKFLOW_ID as string | undefined) ??
  "solution-discovery-workflow";

export interface WizardStage {
  key: string;
  label: string;
  path: string;
  /** One-line explanation of what's happening while this stage is active. */
  description: string;
}

/**
 * Ordered by when the corresponding backend steps actually run, not by the
 * left-nav's ordering - this is the sequence the wizard auto-advances
 * through. Workshop and Replay Center are deliberately excluded: they're
 * ad hoc/historical tools, not stops on the automatic discovery pipeline.
 */
export const WIZARD_STAGES: WizardStage[] = [
  {
    key: "mission-control",
    label: "Mission Control",
    path: "/mission-control",
    description: "Kickoff: agents read the uploaded transcript/documents and begin discovery.",
  },
  {
    key: "requirements",
    label: "Requirement Discovery",
    path: "/requirements",
    description: "Agents extract and structure functional/non-functional requirements, risks, and assumptions.",
  },
  {
    key: "agent-arena",
    label: "Agent Arena",
    path: "/agent-arena",
    description: "Specialist agents apply industry patterns and assess business, technical, and compliance risk in parallel.",
  },
  {
    key: "collaboration-graph",
    label: "Collaboration Graph",
    path: "/collaboration-graph",
    description: "Agents hand off findings to each other while designing the Azure reference and data architecture.",
  },
  {
    key: "architecture-studio",
    label: "Architecture Studio",
    path: "/architecture-studio",
    description: "The end-to-end solution architecture is enriched with UI design, cost, responsible AI, and roadmap input, and the prototype is generated.",
  },
  {
    key: "governance",
    label: "Governance",
    path: "/governance",
    description: "Governance reviews the architecture and requires human approval before final output is produced.",
  },
  {
    key: "final-output",
    label: "Final Output",
    path: "/final-output",
    description: "An executive-ready summary is produced and the prototype/starter kit are ready to download.",
  },
];

const GOVERNANCE_STAGE_INDEX = WIZARD_STAGES.findIndex((stage) => stage.key === "governance");
const FINAL_STAGE_INDEX = WIZARD_STAGES.length - 1;

/** Maps each solution-discovery-workflow step id to a wizard stage index. */
const STEP_TO_STAGE_INDEX: Record<string, number> = {
  "discover-inputs": 0,
  "facilitate-workshop": 0,
  "analyze-requirements": 1,
  "capture-requirements-detail": 1,
  "apply-industry-patterns": 2,
  "assess-risk": 2,
  "assess-risk-compliance": 2,
  "design-architecture": 3,
  "design-data-architecture": 3,
  "design-solution-architecture": 3,
  "propose-innovation": 4,
  "design-ui": 4,
  "optimize-cost": 4,
  "review-responsible-ai": 4,
  "build-roadmap": 4,
  "generate-prototype": 4,
  "governance-review": GOVERNANCE_STAGE_INDEX,
  "coordinate-governance": GOVERNANCE_STAGE_INDEX,
  "summarize-executive": FINAL_STAGE_INDEX,
};

export interface WizardSnapshotSignal {
  workflow_status: WorkflowStatus | null;
  current_workflow_step: string | null;
  approvals: ApprovalRequest[];
}

/**
 * Decides which wizard stage should be active, given the latest Mission
 * Control snapshot. Only ever advances forward automatically (never
 * regresses a manually-reviewed earlier stage), except for the two hard
 * overrides: a pending approval always jumps to Governance, and a
 * completed run always jumps to Final Output.
 */
export function resolveWizardStageIndex(
  snapshot: WizardSnapshotSignal | null,
  previousIndex: number,
): number {
  if (!snapshot) return previousIndex;
  if (snapshot.workflow_status === "completed") return FINAL_STAGE_INDEX;
  if (snapshot.approvals.some((approval) => approval.status === "pending")) {
    return Math.max(previousIndex, GOVERNANCE_STAGE_INDEX);
  }
  if (snapshot.current_workflow_step) {
    const index = STEP_TO_STAGE_INDEX[snapshot.current_workflow_step];
    if (index !== undefined) return Math.max(previousIndex, index);
  }
  return previousIndex;
}
