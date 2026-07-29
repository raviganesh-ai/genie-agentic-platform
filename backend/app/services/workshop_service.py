"""Workshop application service.

Implements the "Workshop Experience"/"Workshop APIs" requirement: every
interaction (chatting with agents, challenging a recommendation,
requesting an alternative architecture, submitting a re-analysis request,
updating priorities) is routed through the unmodified Phase 6
``AgentOrchestrator`` - this service performs no agent reasoning itself.

"Chat" is modeled as supplying the human's message as a step input
variable and resuming the session's workflow run: the message is handed
to the addressed agent(s) exactly like any other step input, and the
agent's own output is whatever the Azure-hosted agent produces during that
resumed run - never synthesized here.
"""
from __future__ import annotations

from app.models.reanalysis_models import ReanalysisRequestType, ReanalysisResult
from app.models.workflow_models import WorkflowRunResult, WorkflowStepInput
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.services.session_service import SessionService

__all__ = ["UnknownWorkflowRunError", "WorkshopService", "create_workshop_service"]


class UnknownWorkflowRunError(RuntimeError):
    """Raised when a workshop action references a workflow run id with no recorded history."""


class WorkshopService:
    """Routes every Workshop Experience interaction through ``AgentOrchestrator``."""

    def __init__(
        self, *, orchestrator: AgentOrchestrator, session_service: SessionService
    ) -> None:
        self._orchestrator = orchestrator
        self._session_service = session_service

    async def chat_with_agent(
        self,
        *,
        session_id: str,
        requesting_user_id: str,
        workflow_run_id: str,
        agent_id: str,
        message: str,
        trace_id: str | None = None,
    ) -> WorkflowRunResult:
        await self._authorize(session_id, requesting_user_id)
        step_id = self._step_id_for_agent(workflow_run_id, agent_id)
        step_inputs = {
            step_id: WorkflowStepInput(step_id=step_id, variables={"user_message": message})
        }
        return await self._orchestrator.resume_workflow(
            workflow_run_id=workflow_run_id,
            session_id=session_id,
            trace_id=trace_id,
            step_inputs=step_inputs,
        )

    async def chat_with_all_agents(
        self,
        *,
        session_id: str,
        requesting_user_id: str,
        workflow_run_id: str,
        message: str,
        trace_id: str | None = None,
    ) -> WorkflowRunResult:
        await self._authorize(session_id, requesting_user_id)
        run = self._get_run(workflow_run_id)
        workflow = self._orchestrator.workflow_registry.get(run.workflow_id)
        step_inputs = {
            step.id: WorkflowStepInput(step_id=step.id, variables={"user_message": message})
            for step in workflow.steps
        }
        return await self._orchestrator.resume_workflow(
            workflow_run_id=workflow_run_id,
            session_id=session_id,
            trace_id=trace_id,
            step_inputs=step_inputs,
        )

    async def regenerate_build_artifacts(
        self,
        *,
        session_id: str,
        requesting_user_id: str,
        workflow_run_id: str,
        instruction: str,
        trace_id: str | None = None,
    ) -> WorkflowRunResult:
        """Re-runs only the ``build-solution`` step with an extra customer
        instruction supplied as its ``user_message`` variable - lets the
        Workshop UI regenerate one generated artifact (or the whole build)
        without resubmitting the entire mission.

        Always targets the fixed ``build-solution`` step id directly,
        rather than resolving it via ``_step_id_for_agent``: every step in
        solution-discovery-workflow executes as ``genie-orchestrator`` (see
        config/workflows/registry.yaml), so an agent-id-based lookup always
        resolves to the first such step instead of this one.
        """
        await self._authorize(session_id, requesting_user_id)
        step_inputs = {
            "build-solution": WorkflowStepInput(
                step_id="build-solution", variables={"user_message": instruction}
            )
        }
        return await self._orchestrator.resume_workflow(
            workflow_run_id=workflow_run_id,
            session_id=session_id,
            trace_id=trace_id,
            step_inputs=step_inputs,
        )

    async def challenge_recommendation(
        self,
        *,
        session_id: str,
        requesting_user_id: str,
        workflow_run_id: str,
        trace_id: str,
        target_recommendation_id: str | None = None,
        rationale: str = "",
    ) -> ReanalysisResult:
        return await self.submit_reanalysis_request(
            session_id=session_id,
            requesting_user_id=requesting_user_id,
            workflow_run_id=workflow_run_id,
            trace_id=trace_id,
            request_type="challenge_recommendation",
            target_recommendation_id=target_recommendation_id,
            rationale=rationale,
        )

    async def request_alternative_architecture(
        self,
        *,
        session_id: str,
        requesting_user_id: str,
        workflow_run_id: str,
        trace_id: str,
        rationale: str = "",
    ) -> ReanalysisResult:
        return await self.submit_reanalysis_request(
            session_id=session_id,
            requesting_user_id=requesting_user_id,
            workflow_run_id=workflow_run_id,
            trace_id=trace_id,
            request_type="request_alternative_architecture",
            rationale=rationale,
        )

    async def update_priorities(
        self,
        *,
        session_id: str,
        requesting_user_id: str,
        workflow_run_id: str,
        trace_id: str,
        rationale: str = "",
    ) -> ReanalysisResult:
        return await self.submit_reanalysis_request(
            session_id=session_id,
            requesting_user_id=requesting_user_id,
            workflow_run_id=workflow_run_id,
            trace_id=trace_id,
            request_type="modify_priorities",
            rationale=rationale,
        )

    async def submit_reanalysis_request(
        self,
        *,
        session_id: str,
        requesting_user_id: str,
        workflow_run_id: str,
        trace_id: str,
        request_type: ReanalysisRequestType,
        target_recommendation_id: str | None = None,
        rationale: str = "",
    ) -> ReanalysisResult:
        await self._authorize(session_id, requesting_user_id)
        return self._orchestrator.request_reanalysis(
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            trace_id=trace_id,
            requested_by=requesting_user_id,
            request_type=request_type,
            target_recommendation_id=target_recommendation_id,
            rationale=rationale,
        )

    async def _authorize(self, session_id: str, requesting_user_id: str) -> None:
        await self._session_service.get_session(
            session_id=session_id, requesting_user_id=requesting_user_id
        )

    def _get_run(self, workflow_run_id: str) -> WorkflowRunResult:
        run = self._orchestrator.get_workflow_run(workflow_run_id)
        if run is None:
            raise UnknownWorkflowRunError(f"Unknown workflow run id '{workflow_run_id}'.")
        return run

    def _step_id_for_agent(self, workflow_run_id: str, agent_id: str) -> str:
        run = self._get_run(workflow_run_id)
        workflow = self._orchestrator.workflow_registry.get(run.workflow_id)
        for step in workflow.steps:
            if step.agent_id == agent_id:
                return step.id
        raise UnknownWorkflowRunError(
            f"No step in workflow '{run.workflow_id}' is assigned to agent '{agent_id}'."
        )


def create_workshop_service(
    *, orchestrator: AgentOrchestrator, session_service: SessionService
) -> WorkshopService:
    return WorkshopService(orchestrator=orchestrator, session_service=session_service)
