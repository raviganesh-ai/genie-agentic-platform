import type { MissionControlSnapshot } from "@/types/missionControl";
import type { AgentDefinition } from "@/types/agent";
import type { WorkflowRunResult } from "@/types/workflow";
import type { ApprovalRequest, GovernanceEvent } from "@/types/governance";
import type { DecisionGraph } from "@/types/collaboration";
import type { SessionReplayResponse } from "@/types/replay";
import type { ArchitectureSnapshot } from "@/types/architecture";
import type { RequirementsQualification } from "@/types/requirementsQualification";

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

export function buildMissionControlSnapshot(): MissionControlSnapshot {
  return {
    session_id: FIXTURE_SESSION_ID,
    workflow_run_id: FIXTURE_WORKFLOW_RUN_ID,
    workflow_status: "running",
    mission_progress: 42,
    active_agents: ["requirements-analyst"],
    completed_agents: [],
    blocked_agents: [],
    current_workflow_step: "extract-requirements",
    timeline: [
      { kind: "workflow_step", label: "Extracted goals", agent_id: "requirements-analyst", timestamp: "2026-07-23T10:00:00Z" },
    ],
    approvals: [],
    handoffs: [],
    memory_updates: [],
    decision_graph: buildDecisionGraph(),
    governance_status: "compliant",
    business_value_score: 70,
    risk_score: 20,
    readiness_score: 55,
  };
}

export function buildAgentDefinitions(): AgentDefinition[] {
  return [
    {
      id: "requirements-analyst",
      name: "Requirements Analyst",
      role: "requirements",
      description: "Extracts requirements from ingested content.",
      capabilities: ["requirement_extraction"],
      allowed_tools: [],
      memory_access: ["shared"],
      model_deployment_ref: "claude-sonnet-5",
      foundry_agent_id: null,
      enabled: true,
      version: "1.0.0",
    },
  ];
}

export function buildWorkflowRunResult(): WorkflowRunResult {
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
  };
}

export function buildApprovalRequests(): ApprovalRequest[] {
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

export function buildArchitectureSnapshot(): ArchitectureSnapshot {
  return {
    session_id: FIXTURE_SESSION_ID,
    workflow_run_id: FIXTURE_WORKFLOW_RUN_ID,
    components: [
      { step_id: "design-architecture", recommended_by: "architecture-designer", content: "Use Azure Container Apps." },
    ],
    decision_graph: buildDecisionGraph(),
  };
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
