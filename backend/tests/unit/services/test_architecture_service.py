"""Unit tests for ``ArchitectureService.get_architecture``.

Guards against a real production bug: every ``solution-discovery-workflow``
step actually executes as ``genie-orchestrator`` (see
``config/workflows/registry.yaml``), so its ``WorkflowStepResult.agent_id``
is always ``"genie-orchestrator"`` - never the real specialist (e.g.
``architecture-designer``) that produced the content. Before the fix,
``ArchitectureService`` filtered ``step_results`` by matching
``result.agent_id`` against agents with the ``architecture_generation``
capability, which could never match ``"genie-orchestrator"`` - so the
Architecture Studio page always rendered zero components. The fix recovers
the true producing specialist from each step's ``allowed_tool_names`` via
``app.agents.tools.orchestration_tools.resolve_delegate_agent_id``.
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.agents.models import AgentDefinition
from app.agents.registry import AgentRegistry
from app.models.workflow_models import WorkflowRunResult, WorkflowStepResult
from app.services.architecture_service import ArchitectureService
from app.services.session_service import create_session_service
from app.workflows.models import WorkflowDefinition, WorkflowStep


class _FakeWorkflowRegistry:
    """Duck-typed stand-in exposing only the ``.get`` method the service uses."""

    def __init__(self, workflow: WorkflowDefinition) -> None:
        self._workflow = workflow

    def get(self, workflow_id: str) -> WorkflowDefinition:
        assert workflow_id == self._workflow.id
        return self._workflow


class _FakeDecisionGraphService:
    def get_graph(self, session_id: str) -> None:
        return None


class _FakeOrchestrator:
    """Duck-typed stand-in exposing only what ``ArchitectureService`` reads."""

    def __init__(
        self,
        *,
        run: WorkflowRunResult,
        workflow: WorkflowDefinition,
        agent_registry: AgentRegistry,
    ) -> None:
        self._run = run
        self.workflow_registry = _FakeWorkflowRegistry(workflow)
        self.agent_registry = agent_registry
        self.decision_graph_service = _FakeDecisionGraphService()

    async def get_workflow_run(self, workflow_run_id: str) -> WorkflowRunResult | None:
        return self._run if workflow_run_id == self._run.workflow_run_id else None


class _FakeSharedMemory:
    """Minimal SharedMemoryStore.read double: returns a single record
    carrying {"output_text": ...} for each pre-seeded key, mirroring what
    orchestration_tools's _delegate really writes for a delegated step the
    instant its real generation finishes - well before genie-orchestrator's
    own official WorkflowStepResult exists."""

    def __init__(self, records_by_key: dict[str, str]) -> None:
        self._records_by_key = records_by_key

    async def read(
        self, *, requesting_agent, session_id: str, trace_id: str, key: str | None = None
    ):
        if key is not None and key in self._records_by_key:
            return [SimpleNamespace(content={"output_text": self._records_by_key[key]})]
        return []


def _agent(agent_id: str, *, capabilities: list[str]) -> AgentDefinition:
    return AgentDefinition(
        id=agent_id, name=agent_id, role="role", description="d", capabilities=capabilities
    )


@pytest.fixture
def agent_registry() -> AgentRegistry:
    return AgentRegistry(
        {
            "genie-orchestrator": _agent("genie-orchestrator", capabilities=["mission_orchestration"]),
            "requirements-analyst": _agent(
                "requirements-analyst", capabilities=["requirement_extraction"]
            ),
            "architecture-designer": _agent(
                "architecture-designer", capabilities=["architecture_generation"]
            ),
        }
    )


@pytest.fixture
def workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        id="solution-discovery-workflow",
        name="Solution Discovery Workflow",
        description="d",
        steps=[
            WorkflowStep(
                id="analyze-requirements",
                agent_id="genie-orchestrator",
                description="d",
                depends_on=[],
                prompt_id="p",
                allowed_tool_names=["call_requirements_analyst"],
            ),
            WorkflowStep(
                id="design-architecture",
                agent_id="genie-orchestrator",
                description="d",
                depends_on=["analyze-requirements"],
                prompt_id="p",
                allowed_tool_names=["call_architecture_designer"],
            ),
        ],
    )


def _step_result(step_id: str, *, output_text: str) -> WorkflowStepResult:
    now = datetime.now(UTC)
    return WorkflowStepResult(
        step_id=step_id,
        agent_id="genie-orchestrator",
        status="completed",
        output_text=output_text,
        started_at=now,
        completed_at=now,
    )


async def test_get_architecture_recovers_component_from_delegated_step(
    agent_registry: AgentRegistry, workflow: WorkflowDefinition
) -> None:
    run = WorkflowRunResult(
        workflow_run_id="run-1",
        workflow_id=workflow.id,
        session_id="session-1",
        status="waiting_for_approval",
        waves=[["analyze-requirements"], ["design-architecture"]],
        step_results=[
            _step_result("analyze-requirements", output_text="Extracted requirements."),
            _step_result("design-architecture", output_text="Recommended Azure architecture."),
        ],
    )
    orchestrator = _FakeOrchestrator(run=run, workflow=workflow, agent_registry=agent_registry)
    session_service = create_session_service(orchestrator=orchestrator)  # type: ignore[arg-type]
    session = await session_service.create_session(owner_user_id="user-1", title="t")
    service = ArchitectureService(orchestrator=orchestrator, session_service=session_service)  # type: ignore[arg-type]

    snapshot = await service.get_architecture(
        session_id=session.id, requesting_user_id="user-1", workflow_run_id="run-1"
    )

    assert [component.step_id for component in snapshot.components] == ["design-architecture"]
    component = snapshot.components[0]
    assert component.recommended_by == "architecture-designer"
    assert component.content == "Recommended Azure architecture."


async def test_get_architecture_excludes_steps_with_no_architecture_delegation(
    agent_registry: AgentRegistry, workflow: WorkflowDefinition
) -> None:
    run = WorkflowRunResult(
        workflow_run_id="run-1",
        workflow_id=workflow.id,
        session_id="session-1",
        status="completed",
        waves=[["analyze-requirements"]],
        step_results=[_step_result("analyze-requirements", output_text="Extracted requirements.")],
    )
    orchestrator = _FakeOrchestrator(run=run, workflow=workflow, agent_registry=agent_registry)
    session_service = create_session_service(orchestrator=orchestrator)  # type: ignore[arg-type]
    session = await session_service.create_session(owner_user_id="user-1", title="t")
    service = ArchitectureService(orchestrator=orchestrator, session_service=session_service)  # type: ignore[arg-type]

    snapshot = await service.get_architecture(
        session_id=session.id, requesting_user_id="user-1", workflow_run_id="run-1"
    )

    assert snapshot.components == []


async def test_get_architecture_falls_back_to_shared_memory_before_official_completion(
    agent_registry: AgentRegistry, workflow: WorkflowDefinition
) -> None:
    """design-architecture has NO entry at all yet in run.step_results (the
    common case right after its live step_completed SSE event fires, well
    before genie-orchestrator's own slower echo-completion turn resolves
    the official WorkflowStepResult) - the real specialist output already
    written to Shared Collaboration Memory must still surface immediately."""

    run = WorkflowRunResult(
        workflow_run_id="run-1",
        workflow_id=workflow.id,
        session_id="session-1",
        status="running",
        waves=[["analyze-requirements"], ["design-architecture"]],
        step_results=[
            _step_result("analyze-requirements", output_text="Extracted requirements."),
        ],
    )
    orchestrator = _FakeOrchestrator(run=run, workflow=workflow, agent_registry=agent_registry)
    orchestrator.memory_service = SimpleNamespace(
        shared=_FakeSharedMemory({"design-architecture": "Recommended Azure architecture."})
    )
    session_service = create_session_service(orchestrator=orchestrator)  # type: ignore[arg-type]
    session = await session_service.create_session(owner_user_id="user-1", title="t")
    service = ArchitectureService(orchestrator=orchestrator, session_service=session_service)  # type: ignore[arg-type]

    snapshot = await service.get_architecture(
        session_id=session.id, requesting_user_id="user-1", workflow_run_id="run-1"
    )

    assert [component.step_id for component in snapshot.components] == ["design-architecture"]
    component = snapshot.components[0]
    assert component.recommended_by == "architecture-designer"
    assert component.content == "Recommended Azure architecture."
