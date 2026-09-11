"""Unit tests for WorkflowStepExecutor's Shared Collaboration Memory-backed
`step:<id>` variable resolution (the read side of the agent-to-agent
handoff mechanism; the write side is exercised in
tests/unit/agents/tools/test_orchestration_tools.py)."""
from __future__ import annotations

from app.agents.models import AgentDefinition
from app.memory.memory_service import create_memory_service
from app.orchestration.workflow_step_executor import WorkflowStepExecutor
from app.services.model_catalog_service import ModelCatalog
from app.workflows.models import WorkflowStep


def _agent(agent_id: str = "genie-orchestrator", memory_access: list[str] | None = None) -> AgentDefinition:
    return AgentDefinition(
        id=agent_id,
        name=agent_id,
        role="test_role",
        description="A test agent.",
        memory_access=memory_access if memory_access is not None else ["shared"],
    )


def _executor(local_settings) -> WorkflowStepExecutor:
    memory_service = create_memory_service(settings=local_settings)
    return WorkflowStepExecutor(
        agent_registry=None,  # unused by _resolve_variables/_read_step_output
        prompt_registry=None,
        agent_gateway=None,
        governance_service=None,
        memory_service=memory_service,
    )


def _step() -> WorkflowStep:
    return WorkflowStep(
        id="design-architecture",
        agent_id="genie-orchestrator",
        description="Design the architecture.",
        depends_on=["analyze-requirements"],
        variable_sources={"approved_requirements": "step:analyze-requirements"},
    )


async def test_resolve_variables_prefers_shared_memory_over_in_process_step_outputs(
    local_settings,
) -> None:
    executor = _executor(local_settings)
    agent = _agent()
    await executor._memory_service.shared.write(
        agent=agent,
        session_id="session-1",
        trace_id="trace-1",
        key="analyze-requirements",
        classification="requirement",
        content={"output_text": "The real requirements, written to shared memory."},
    )

    resolved = await executor._resolve_variables(
        step=_step(),
        transcript_text="",
        step_outputs={"analyze-requirements": "A stale/paraphrased in-process value."},
        step_input=None,
        agent=agent,
        session_id="session-1",
        trace_id="trace-1",
    )

    assert resolved["approved_requirements"] == "The real requirements, written to shared memory."


async def test_resolve_variables_falls_back_to_step_outputs_when_memory_has_no_record(
    local_settings,
) -> None:
    executor = _executor(local_settings)
    agent = _agent()

    resolved = await executor._resolve_variables(
        step=_step(),
        transcript_text="",
        step_outputs={"analyze-requirements": "Only available in-process."},
        step_input=None,
        agent=agent,
        session_id="session-1",
        trace_id="trace-1",
    )

    assert resolved["approved_requirements"] == "Only available in-process."


async def test_resolve_variables_falls_back_when_agent_not_authorized_for_shared_memory(
    local_settings,
) -> None:
    executor = _executor(local_settings)
    writer = _agent(agent_id="genie-orchestrator")
    unauthorized_reader = _agent(agent_id="genie-orchestrator", memory_access=["personal"])
    await executor._memory_service.shared.write(
        agent=writer,
        session_id="session-1",
        trace_id="trace-1",
        key="analyze-requirements",
        classification="requirement",
        content={"output_text": "Should not be readable by this agent."},
    )

    resolved = await executor._resolve_variables(
        step=_step(),
        transcript_text="",
        step_outputs={"analyze-requirements": "The in-process fallback."},
        step_input=None,
        agent=unauthorized_reader,
        session_id="session-1",
        trace_id="trace-1",
    )

    assert resolved["approved_requirements"] == "The in-process fallback."


async def test_resolve_variables_reads_exact_variable_from_completed_step(
    local_settings,
) -> None:
    executor = _executor(local_settings)
    step = WorkflowStep(
        id="build-solution",
        agent_id="genie-orchestrator",
        description="Build the approved solution.",
        depends_on=["design-architecture"],
        variable_sources={
            "requirements": "step-variable:design-architecture:approved_requirements"
        },
    )

    resolved = await executor._resolve_variables(
        step=step,
        transcript_text="",
        step_outputs={},
        step_variables={
            "design-architecture": {
                "approved_requirements": "The user's edited and approved requirements."
            }
        },
        step_input=None,
        agent=_agent(),
        session_id="session-1",
        trace_id="trace-1",
    )

    assert resolved["requirements"] == "The user's edited and approved requirements."


class _FakeModelCatalogService:
    def __init__(self, *, available_models: list[str], default_model: str) -> None:
        self._catalog = ModelCatalog(available_models=available_models, default_model=default_model)

    async def get_catalog(self) -> ModelCatalog:
        return self._catalog


async def test_resolve_variables_resolves_model_catalog_source(local_settings) -> None:
    # Genie must give the Build Agent the real, live Foundry model catalog
    # (see config/workflows/registry.yaml's build-solution step) instead of
    # letting it invent fake model-ID strings for any generated dropdown.
    executor = WorkflowStepExecutor(
        agent_registry=None,
        prompt_registry=None,
        agent_gateway=None,
        governance_service=None,
        model_catalog_service=_FakeModelCatalogService(
            available_models=["gpt-4o", "claude-opus-4"], default_model="gpt-4o"
        ),
    )
    step = WorkflowStep(
        id="build-solution",
        agent_id="genie-orchestrator",
        description="Build the approved solution.",
        depends_on=["design-architecture"],
        variable_sources={"available_models": "model-catalog"},
    )

    resolved = await executor._resolve_variables(
        step=step,
        transcript_text="",
        step_outputs={},
        step_input=None,
        agent=_agent(),
        session_id="session-1",
        trace_id="trace-1",
    )

    assert resolved["available_models"] == "gpt-4o, claude-opus-4 (platform default: gpt-4o)"


async def test_resolve_variables_omits_model_catalog_when_no_service_is_wired(local_settings) -> None:
    executor = WorkflowStepExecutor(
        agent_registry=None,
        prompt_registry=None,
        agent_gateway=None,
        governance_service=None,
    )
    step = WorkflowStep(
        id="build-solution",
        agent_id="genie-orchestrator",
        description="Build the approved solution.",
        depends_on=["design-architecture"],
        variable_sources={"available_models": "model-catalog"},
    )

    resolved = await executor._resolve_variables(
        step=step,
        transcript_text="",
        step_outputs={},
        step_input=None,
        agent=_agent(),
        session_id="session-1",
        trace_id="trace-1",
    )

    assert "available_models" not in resolved

