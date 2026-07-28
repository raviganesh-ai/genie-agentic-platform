import type { WorkflowRunResult } from "@/types/workflow";
import type { ApprovalRequest, GovernanceEvent } from "@/types/governance";
import type { DecisionGraph } from "@/types/collaboration";
import type { SessionReplayResponse } from "@/types/replay";
import type { ArchitectureSnapshot } from "@/types/architecture";
import type { RequirementsQualification } from "@/types/requirementsQualification";
import type { AgentSummary } from "@/types/agents";

/** Shared, non-customer fixture data for frontend tests only. */

export const FIXTURE_SESSION_ID = "session-fixture-1";
export const FIXTURE_WORKFLOW_RUN_ID = "run-fixture-1";

export function buildDecisionGraph(): DecisionGraph {
  return {
    session_id: FIXTURE_SESSION_ID,
    nodes: [
      { id: "node-1", node_type: "agent", label: "Requirements Analyst", session_id: FIXTURE_SESSION_ID, metadata: {} },
      { id: "node-2", node_type: "recommendation", label: "Recommendation A", session_id: FIXTURE_SESSION_ID, metadata: {} },
    ],
    edges: [
      { id: "edge-1", edge_type: "agent_to_agent", source_id: "node-1", target_id: "node-2", session_id: FIXTURE_SESSION_ID, metadata: {} },
    ],
  };
}

export function buildWorkflowRunResult(
  overrides: Partial<WorkflowRunResult> = {},
): WorkflowRunResult {
  return {
    workflow_run_id: FIXTURE_WORKFLOW_RUN_ID,
    workflow_id: "solution-discovery-workflow",
    session_id: FIXTURE_SESSION_ID,
    status: "completed",
    waves: [["requirements-analyst"]],
    step_results: [
      {
        step_id: "extract-requirements",
        agent_id: "requirements-analyst",
        status: "completed",
        output_text: "Identified 3 goals.",
        error: null,
        started_at: "2026-07-23T10:00:00Z",
        completed_at: "2026-07-23T10:01:00Z",
      },
    ],
    detail: "",
    ...overrides,
  };
}

export function buildApprovalRequests(
  overrides: Partial<ApprovalRequest> = {},
): ApprovalRequest[] {
  return [
    {
      id: "approval-1",
      checkpoint_id: "architecture-approval",
      session_id: FIXTURE_SESSION_ID,
      trace_id: "trace-1",
      requested_by_agent_id: "architecture-designer",
      subject_type: "workflow_step",
      subject_id: "design-architecture",
      status: "pending",
      requested_at: "2026-07-23T10:05:00Z",
      expires_at: null,
      ...overrides,
    },
  ];
}

export function buildGovernanceEvents(): GovernanceEvent[] {
  return [
    {
      id: "event-1",
      category: "agent_execution",
      session_id: FIXTURE_SESSION_ID,
      trace_id: "trace-1",
      agent_id: "requirements-analyst",
      timestamp: "2026-07-23T10:00:30Z",
      detail: {},
    },
  ];
}

export function buildSessionReplayResponse(): SessionReplayResponse {
  return {
    session_id: FIXTURE_SESSION_ID,
    governance_events: buildGovernanceEvents(),
    recommendation_lineage: [],
    approval_requests: buildApprovalRequests(),
    approval_decisions: [],
    approval_audit_trail: [
      {
        id: "audit-1",
        request_id: "approval-1",
        session_id: FIXTURE_SESSION_ID,
        trace_id: "trace-1",
        event: "requested",
        timestamp: "2026-07-23T10:05:00Z",
        actor: "architecture-designer",
        detail: "",
      },
    ],
    decision_graph: buildDecisionGraph(),
  };
}

export const FIXTURE_ARCHITECTURE_CONTENT = `## UI Design
The customer-facing app needs the following screens.
- **Upload Screen**: Lets the customer upload source documents; fulfills the document intake requirement.

## Multi-Agent Workflow
This solution needs the following specialist agents.
- **Document Intake Agent**: Normalizes incoming documents and hands off to the Classification Agent.
- **Classification Agent**: Classifies each document into a predefined type.

## Azure Reference Architecture
The following Azure services support the app and agents above.
- **Azure Container Apps**: Use Azure Container Apps to host the agents; managed identity for auth.`;

export function buildArchitectureSnapshot(): ArchitectureSnapshot {
  return {
    session_id: FIXTURE_SESSION_ID,
    workflow_run_id: FIXTURE_WORKFLOW_RUN_ID,
    components: [
      {
        step_id: "design-architecture",
        recommended_by: "architecture-designer",
        content: FIXTURE_ARCHITECTURE_CONTENT,
      },
    ],
    decision_graph: buildDecisionGraph(),
  };
}

export function buildAgentSummaries(): AgentSummary[] {
  return [
    {
      id: "genie-orchestrator",
      name: "Genie Orchestrator",
      role: "mission_orchestration",
      description: "Drives the mission phase by phase, delegating to each specialist agent.",
      connected_agent_ids: ["requirements-analyst", "architecture-designer"],
      enabled: true,
    },
    {
      id: "requirements-analyst",
      name: "Requirements Analyst",
      role: "requirement_discovery",
      description: "Extracts goals, requirements, risks, and constraints from ingested material.",
      connected_agent_ids: null,
      enabled: true,
    },
    {
      id: "architecture-designer",
      name: "Architecture Designer",
      role: "architecture_design",
      description: "Produces interactive Azure reference architectures with rationale.",
      connected_agent_ids: ["build-agent", "governance-reviewer", "deployment-agent"],
      enabled: true,
    },
    {
      id: "build-agent",
      name: "Build Agent",
      role: "solution_build",
      description: "Generates the customer-facing UI code and the dedicated multi-agent workflow.",
      connected_agent_ids: null,
      enabled: true,
    },
    {
      id: "governance-reviewer",
      name: "Governance Reviewer",
      role: "governance",
      description: "Evaluates generated artifacts against governance and security policies.",
      connected_agent_ids: null,
      enabled: true,
    },
    {
      id: "deployment-agent",
      name: "Deployment Agent",
      role: "solution_deployment",
      description: "Provisions the agents and UI, and returns a launch link.",
      connected_agent_ids: null,
      enabled: true,
    },
  ];
}

export function buildRequirementsQualification(
  overrides: Partial<RequirementsQualification> = {},
): RequirementsQualification {
  return {
    status: "qualified",
    reason: "Multiple specialist agents must collaborate on open-ended architecture decisions.",
    assessed_by_agent_id: "requirements-analyst",
    ...overrides,
  };
}
