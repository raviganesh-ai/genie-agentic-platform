"""Architecture Studio application service.

Assembles the current interactive architecture view for a session from
already-recorded workflow step outputs and the decision graph - performs
no architecture design/reasoning itself. Alternative design generation is
routed through the unmodified Phase 6 ``AgentOrchestrator.request_reanalysis``
(request type ``"request_alternative_architecture"``); the actual
redesign is produced by whichever registered agent
``ReanalysisService`` routes to, on a subsequent workflow run/resume.

Each architecture "component" corresponds to one completed workflow step
whose configured ``allowed_tool_names`` delegates to a specialist agent
whose registered capability is ``architecture_generation`` (every
``solution-discovery-workflow`` step actually executes as
``genie-orchestrator``, which then calls exactly one delegation tool for
its phase - see ``app.agents.tools.orchestration_tools`` - so the real
specialist is recovered from the step's tool wiring, never from
``WorkflowStepResult.agent_id`` itself). Its full ``output_text``
(rationale, security considerations, dependencies, and cost notes are all
part of that one agent-produced blob - there is no further structured
schema to parse it into, since no prior phase defined one) is returned
as-is, alongside which specialist agent produced it.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.agents.registry import AgentRegistry
from app.agents.tools.orchestration_tools import resolve_delegate_agent_id
from app.models.decision_graph import DecisionGraph
from app.models.reanalysis_models import ReanalysisResult
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.services.session_service import SessionService
from app.services.workshop_service import UnknownWorkflowRunError
from app.workflows.models import WorkflowStep

__all__ = [
    "ArchitectureComponent",
    "ArchitectureService",
    "ArchitectureSnapshot",
    "create_architecture_service",
]

_ARCHITECTURE_CAPABILITY = "architecture_generation"


def _architecture_agent_id_for_step(
    step: WorkflowStep | None, agent_registry: AgentRegistry
) -> str | None:
    """Returns the specialist agent id ``step`` delegates architecture work to.

    Every ``solution-discovery-workflow`` step invokes ``genie-orchestrator``
    directly (see ``config/workflows/registry.yaml``), so its own
    ``WorkflowStepResult.agent_id`` is always ``"genie-orchestrator"`` -
    never the real specialist that produced the content (e.g.
    ``architecture-designer``). The real specialist is recovered from the
    step's ``allowed_tool_names`` instead.
    """

    if step is None:
        return None
    for tool_name in step.allowed_tool_names or []:
        target_agent_id = resolve_delegate_agent_id(tool_name)
        if target_agent_id is None:
            continue
        if _ARCHITECTURE_CAPABILITY in agent_registry.get(target_agent_id).capabilities:
            return target_agent_id
    return None


class ArchitectureComponent(BaseModel):
    """One agent-produced architecture recommendation within a workflow run."""

    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(min_length=1)
    recommended_by: str = Field(min_length=1)
    content: str


class ArchitectureSnapshot(BaseModel):
    """The current interactive architecture view for one session."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1)
    workflow_run_id: str = Field(min_length=1)
    components: list[ArchitectureComponent] = Field(default_factory=list)
    decision_graph: DecisionGraph | None = None


class ArchitectureService:
    """Reads the current architecture view and routes alternative design requests."""

    def __init__(
        self, *, orchestrator: AgentOrchestrator, session_service: SessionService
    ) -> None:
        self._orchestrator = orchestrator
        self._session_service = session_service

    async def get_architecture(
        self, *, session_id: str, requesting_user_id: str, workflow_run_id: str
    ) -> ArchitectureSnapshot:
        await self._session_service.get_session(
            session_id=session_id, requesting_user_id=requesting_user_id
        )
        run = await self._orchestrator.get_workflow_run(workflow_run_id)
        if run is None:
            raise UnknownWorkflowRunError(f"Unknown workflow run id '{workflow_run_id}'.")

        steps_by_id = {step.id: step for step in self._orchestrator.workflow_registry.get(run.workflow_id).steps}
        components: list[ArchitectureComponent] = []
        for result in run.step_results:
            if result.status != "completed":
                continue
            recommended_by = _architecture_agent_id_for_step(
                steps_by_id.get(result.step_id), self._orchestrator.agent_registry
            )
            if recommended_by is None:
                continue
            components.append(
                ArchitectureComponent(
                    step_id=result.step_id,
                    recommended_by=recommended_by,
                    content=result.output_text or "",
                )
            )

        return ArchitectureSnapshot(
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            components=components,
            decision_graph=self._orchestrator.decision_graph_service.get_graph(session_id),
        )

    async def request_alternative(
        self,
        *,
        session_id: str,
        requesting_user_id: str,
        workflow_run_id: str,
        trace_id: str,
        rationale: str = "",
    ) -> ReanalysisResult:
        await self._session_service.get_session(
            session_id=session_id, requesting_user_id=requesting_user_id
        )
        return self._orchestrator.request_reanalysis(
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            trace_id=trace_id,
            requested_by=requesting_user_id,
            request_type="request_alternative_architecture",
            rationale=rationale,
        )


def create_architecture_service(
    *, orchestrator: AgentOrchestrator, session_service: SessionService
) -> ArchitectureService:
    return ArchitectureService(orchestrator=orchestrator, session_service=session_service)
