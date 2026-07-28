"""Unit tests for WorkflowStepExecutor's Shared Collaboration Memory-backed
`step:<id>` variable resolution (the read side of the agent-to-agent
handoff mechanism; the write side is exercised in
tests/unit/agents/tools/test_orchestration_tools.py)."""
from __future__ import annotations

from app.agents.models import AgentDefinition
from app.memory.memory_service import create_memory_service
from app.orchestration.workflow_step_executor import WorkflowStepExecutor
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
