"""Mission Control snapshot assembly service.

Builds the ``MissionControlSnapshot`` - "the primary UI contract" for the
Mission Control Dashboard - purely by reading already-recorded state from
the unmodified Phase 1-6 services exposed on ``AgentOrchestrator``
(workflow run history, handoffs, decision graph, approvals, governance
events, shared memory). Performs no orchestration or business reasoning of
its own.
"""
from __future__ import annotations

from uuid import uuid4

from app.agents.models import AgentDefinition
from app.memory.memory_models import MemoryAccessDeniedError
from app.models.mission_control_snapshot import (
    MemoryUpdateSummary,
    MissionControlSnapshot,
    TimelineEntry,
)
from app.models.workflow_models import WorkflowRunResult
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.services.session_service import SessionService

__all__ = ["MissionControlService", "create_mission_control_service"]

# A resolved-in-progress or not-yet-attempted workflow status: while a run is
# in one of these states, steps in its next pending wave are considered
# "active" (waiting on an agent/approval), matching the Agent Arena status
# values in the build spec (Idle/Analyzing/.../WaitingForApproval/Blocked).
_IN_PROGRESS_STATUSES = {"pending", "running", "waiting_for_agent", "waiting_for_approval"}


def _synthetic_ui_agent(user_id: str) -> AgentDefinition:
    """A read-only identity representing an authenticated UI user.

    Shared Collaboration Memory reads are policy-gated per-agent (see
    ``MemoryAccessPolicyService``); the Mission Control UI has no agent
    identity of its own; this synthetic, non-persisted identity lets a
    human user's read-only queries pass through the same policy/governance
    path every agent's reads do, without granting any write capability
    (``memory_access`` includes only "shared").
    """

    return AgentDefinition(
        id=f"ui-viewer:{user_id}",
        name="Mission Control Viewer",
        role="ui_viewer",
        description="Synthetic identity for authenticated UI read-only memory queries.",
        memory_access=["shared"],
        enabled=True,
    )


class MissionControlService:
    """Assembles ``MissionControlSnapshot``s for a session."""

    def __init__(self, *, orchestrator: AgentOrchestrator, session_service: SessionService) -> None:
        self._orchestrator = orchestrator
        self._session_service = session_service

    async def get_snapshot(
        self, *, session_id: str, requesting_user_id: str
    ) -> MissionControlSnapshot:
        await self._session_service.get_session(
            session_id=session_id, requesting_user_id=requesting_user_id
        )

        runs = self._orchestrator.list_workflow_runs(session_id)
        run: WorkflowRunResult | None = runs[-1] if runs else None

        completed_agents, blocked_agents, timeline = self._step_summary(run)
        active_agents, current_step = self._active_agents(run)

        approvals = await self._orchestrator.approval_service.list_requests_for_session(session_id)
        handoffs = (
            self._orchestrator.handoff_service.handoffs_for_run(run.workflow_run_id)
            if run is not None
            else []
        )
        for handoff in handoffs:
            timeline.append(
                TimelineEntry(
                    kind="handoff",
                    label=f"{handoff.source_agent_id} -> {handoff.target_agent_id}: {handoff.reason}",
                    agent_id=handoff.source_agent_id,
                    timestamp=handoff.timestamp,
                )
            )
        timeline.sort(key=lambda entry: entry.timestamp)

        decision_graph = self._orchestrator.decision_graph_service.get_graph(session_id)
        governance_events = await self._orchestrator.governance_service.events_for_session(
            session_id
        )
        governance_status = (
            "attention_required"
            if any(event.category == "access_denied" for event in governance_events)
            else "compliant"
        )

        memory_updates = await self._memory_updates(session_id, requesting_user_id)

        total_steps = sum(len(wave) for wave in run.waves) if run is not None else 0
        completed_count = sum(
            1 for result in (run.step_results if run else []) if result.status == "completed"
        )
        failed_count = sum(
            1 for result in (run.step_results if run else []) if result.status == "failed"
        )
        readiness_score = (completed_count / total_steps) if total_steps else 0.0
        risk_score = (failed_count / total_steps) if total_steps else 0.0

        decided_approvals = [a for a in approvals if a.status in ("approved", "rejected")]
        business_value_score = (
            sum(1 for a in decided_approvals if a.status == "approved") / len(decided_approvals)
            if decided_approvals
            else 1.0
        )

        return MissionControlSnapshot(
            session_id=session_id,
            workflow_run_id=run.workflow_run_id if run else None,
            workflow_status=run.status if run else None,
            mission_progress=readiness_score,
            active_agents=active_agents,
            completed_agents=sorted(set(completed_agents)),
            blocked_agents=sorted(set(blocked_agents)),
            current_workflow_step=current_step,
            timeline=timeline,
            approvals=approvals,
            handoffs=handoffs,
            memory_updates=memory_updates,
            decision_graph=decision_graph,
            governance_status=governance_status,
            business_value_score=business_value_score,
            risk_score=risk_score,
            readiness_score=readiness_score,
        )

    def _step_summary(
        self, run: WorkflowRunResult | None
    ) -> tuple[list[str], list[str], list[TimelineEntry]]:
        if run is None:
            return [], [], []

        completed_agents = [r.agent_id for r in run.step_results if r.status == "completed"]
        blocked_agents = [r.agent_id for r in run.step_results if r.status == "failed"]
        timeline = [
            TimelineEntry(
                kind="workflow_step",
                label=f"{result.step_id} ({result.status})",
                agent_id=result.agent_id,
                timestamp=result.completed_at,
            )
            for result in run.step_results
        ]
        return completed_agents, blocked_agents, timeline

    def _active_agents(self, run: WorkflowRunResult | None) -> tuple[list[str], str | None]:
        if run is None or run.status not in _IN_PROGRESS_STATUSES:
            return [], None

        completed_step_ids = {result.step_id for result in run.step_results}
        for wave in run.waves:
            pending = [step_id for step_id in wave if step_id not in completed_step_ids]
            if pending:
                agent_ids = [
                    agent_id
                    for step_id in pending
                    if (agent_id := self._agent_id_for_step(run.workflow_id, step_id)) is not None
                ]
                return sorted(set(agent_ids)), pending[0]
        return [], None

    def _agent_id_for_step(self, workflow_id: str, step_id: str) -> str | None:
        try:
            workflow = self._orchestrator.workflow_registry.get(workflow_id)
        except KeyError:
            return None
        for step in workflow.steps:
            if step.id == step_id:
                return step.agent_id
        return None

    async def _memory_updates(
        self, session_id: str, requesting_user_id: str
    ) -> list[MemoryUpdateSummary]:
        ui_agent = _synthetic_ui_agent(requesting_user_id)
        try:
            records = await self._orchestrator.memory_service.shared.read(
                requesting_agent=ui_agent,
                session_id=session_id,
                trace_id=str(uuid4()),
            )
        except MemoryAccessDeniedError:
            return []
        return [
            MemoryUpdateSummary(
                key=record.id,
                classification=record.classification,
                agent_id=record.lineage.agent_id,
                version=record.version,
                timestamp=record.lineage.timestamp,
            )
            for record in records
        ]


def create_mission_control_service(
    *, orchestrator: AgentOrchestrator, session_service: SessionService
) -> MissionControlService:
    return MissionControlService(orchestrator=orchestrator, session_service=session_service)
