"""Unit tests for genie-orchestrator's delegation function tools."""
from __future__ import annotations

from typing import Any

import pytest

from app.agents.models import AgentDefinition, AgentExecutionRequest, AgentExecutionResult
from app.agents.tool_execution import AgentToolRegistry, ToolCallContext, ToolExecutionError
from app.agents.tools.orchestration_tools import register_orchestrator_delegation_tools


class _RecordingAgentGateway:
    def __init__(self, *, output_text: str = "The real specialist output.") -> None:
        self.requests: list[AgentExecutionRequest] = []
        self._output_text = output_text

    async def execute(self, request: AgentExecutionRequest) -> AgentExecutionResult:
        self.requests.append(request)
        return AgentExecutionResult(
            agent_id=request.agent_id,
            output_text=self._output_text,
            correlation_id=request.correlation_id,
        )


class _RecordingGovernanceService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def record_execution(
        self, *, session_id: str, trace_id: str, agent_id: str, detail: dict[str, Any] | None = None
    ) -> None:
        self.calls.append(
            {"session_id": session_id, "trace_id": trace_id, "agent_id": agent_id, "detail": detail}
        )


def _orchestrator_agent() -> AgentDefinition:
    return AgentDefinition(
        id="genie-orchestrator",
        name="Genie Orchestrator",
        role="mission_orchestration",
        description="Drives the mission.",
        foundry_agent_id="genie-orchestrator",
    )


class _FakeAgentRegistry:
    def __init__(self, agents: dict[str, AgentDefinition]) -> None:
        self._agents = agents

    def get(self, agent_id: str) -> AgentDefinition:
        return self._agents[agent_id]


class _FakeSharedMemoryStore:
    def __init__(self) -> None:
        self.writes: list[dict[str, Any]] = []

    async def write(self, **kwargs: Any) -> None:
        self.writes.append(kwargs)


class _FakeMemoryService:
    def __init__(self) -> None:
        self.shared = _FakeSharedMemoryStore()


def _requirements_analyst_agent() -> AgentDefinition:
    return AgentDefinition(
        id="requirements-analyst",
        name="Requirements Analyst",
        role="requirement_discovery",
        description="Extracts requirements.",
        foundry_agent_id="requirements-analyst",
        memory_access=["shared"],
    )


async def test_call_requirements_analyst_delegates_and_records_governance_event():
    registry = AgentToolRegistry()
    gateway = _RecordingAgentGateway(output_text="Extracted requirements.")
    governance_service = _RecordingGovernanceService()
    register_orchestrator_delegation_tools(
        registry, agent_gateway=gateway, governance_service=governance_service
    )
    context = ToolCallContext(
        agent=_orchestrator_agent(),
        session_id="session-1",
        trace_id="run-1:analyze-requirements",
    )

    result = await registry.execute(
        agent_id="genie-orchestrator",
        tool_name="call_requirements_analyst",
        arguments={"transcript_excerpt": "We need a chatbot.", "user_message": ""},
        context=context,
    )

    assert result == {"output_text": "Extracted requirements."}
    [request] = gateway.requests
    assert request.agent_id == "requirements-analyst"
    assert request.prompt_id == "requirements-extraction-v1"
    assert request.variables == {"transcript_excerpt": "We need a chatbot.", "user_message": ""}
    assert request.correlation_id == "run-1:analyze-requirements"
    assert request.session_id == "session-1"

    [event] = governance_service.calls
    assert event["agent_id"] == "requirements-analyst"
    assert event["session_id"] == "session-1"
    assert event["detail"]["step_id"] == "analyze-requirements"
    assert event["detail"]["delegated_by"] == "genie-orchestrator"
    assert event["detail"]["output_preview"] == "Extracted requirements."


async def test_call_deployment_agent_forwards_all_declared_variables():
    registry = AgentToolRegistry()
    gateway = _RecordingAgentGateway(output_text="Launch summary ready.")
    governance_service = _RecordingGovernanceService()
    register_orchestrator_delegation_tools(
        registry, agent_gateway=gateway, governance_service=governance_service
    )
    context = ToolCallContext(agent=_orchestrator_agent(), session_id="session-1", trace_id="trace-1")

    await registry.execute(
        agent_id="genie-orchestrator",
        tool_name="call_deployment_agent",
        arguments={
            "build_output": "The build.",
            "governance_decision": "APPROVED",
            "user_message": "",
        },
        context=context,
    )

    [request] = gateway.requests
    assert request.agent_id == "deployment-agent"
    assert request.prompt_id == "deployment-v1"
    assert request.variables == {
        "build_output": "The build.",
        "governance_decision": "APPROVED",
        "user_message": "",
    }


async def test_delegation_tool_fails_closed_without_a_session_id():
    registry = AgentToolRegistry()
    gateway = _RecordingAgentGateway()
    governance_service = _RecordingGovernanceService()
    register_orchestrator_delegation_tools(
        registry, agent_gateway=gateway, governance_service=governance_service
    )
    context = ToolCallContext(agent=_orchestrator_agent(), session_id=None, trace_id="trace-1")

    with pytest.raises(ToolExecutionError, match="requires an active session_id"):
        await registry.execute(
            agent_id="genie-orchestrator",
            tool_name="call_requirements_analyst",
            arguments={"transcript_excerpt": "x", "user_message": ""},
            context=context,
        )

    assert gateway.requests == []
    assert governance_service.calls == []


async def test_delegation_tool_rejects_non_string_argument():
    registry = AgentToolRegistry()
    gateway = _RecordingAgentGateway()
    governance_service = _RecordingGovernanceService()
    register_orchestrator_delegation_tools(
        registry, agent_gateway=gateway, governance_service=governance_service
    )
    context = ToolCallContext(agent=_orchestrator_agent(), session_id="session-1", trace_id="trace-1")

    with pytest.raises(ToolExecutionError, match="to be a string"):
        await registry.execute(
            agent_id="genie-orchestrator",
            tool_name="call_requirements_analyst",
            arguments={"transcript_excerpt": 123, "user_message": ""},
            context=context,
        )


async def test_delegation_prefers_callers_own_resolved_variable_over_model_argument():
    """The model may paraphrase/truncate a large upstream output instead of
    copying it verbatim into the tool-call argument. `ToolCallContext.variables`
    (genie-orchestrator's own resolved prompt variables for this step) must
    win so the delegated specialist always receives the real upstream text."""

    registry = AgentToolRegistry()
    gateway = _RecordingAgentGateway(output_text="Architecture recommendation.")
    governance_service = _RecordingGovernanceService()
    register_orchestrator_delegation_tools(
        registry, agent_gateway=gateway, governance_service=governance_service
    )
    context = ToolCallContext(
        agent=_orchestrator_agent(),
        session_id="session-1",
        trace_id="trace-1",
        variables={
            "approved_requirements": "The real, full requirements output from step 1.",
            "user_message": "",
        },
    )

    await registry.execute(
        agent_id="genie-orchestrator",
        tool_name="call_architecture_designer",
        arguments={"approved_requirements": "a paraphrased summary", "user_message": ""},
        context=context,
    )

    [request] = gateway.requests
    assert request.agent_id == "architecture-designer"
    assert request.variables == {
        "approved_requirements": "The real, full requirements output from step 1.",
        "user_message": "",
    }


async def test_delegation_event_omits_step_id_when_trace_id_has_no_step_suffix():
    """A trace_id with no `workflow_run_id:step_id` structure (not produced by
    a running workflow step) should not crash - step_id is simply omitted."""

    registry = AgentToolRegistry()
    gateway = _RecordingAgentGateway(output_text="Extracted requirements.")
    governance_service = _RecordingGovernanceService()
    register_orchestrator_delegation_tools(
        registry, agent_gateway=gateway, governance_service=governance_service
    )
    context = ToolCallContext(agent=_orchestrator_agent(), session_id="session-1", trace_id="trace-1")

    await registry.execute(
        agent_id="genie-orchestrator",
        tool_name="call_requirements_analyst",
        arguments={"transcript_excerpt": "x", "user_message": ""},
        context=context,
    )

    [event] = governance_service.calls
    assert event["detail"]["step_id"] is None


async def test_delegation_writes_the_specialists_real_output_to_shared_memory():
    """This is the actual Shared Collaboration Memory handoff mechanism: once
    requirements-analyst returns, its real output is written to Shared Memory
    keyed by the workflow step id, so a downstream step's agent (e.g.
    architecture-designer) can read it back - not just an in-process value."""

    registry = AgentToolRegistry()
    gateway = _RecordingAgentGateway(output_text="Extracted requirements.")
    governance_service = _RecordingGovernanceService()
    memory_service = _FakeMemoryService()
    agent_registry = _FakeAgentRegistry({"requirements-analyst": _requirements_analyst_agent()})
    register_orchestrator_delegation_tools(
        registry,
        agent_gateway=gateway,
        governance_service=governance_service,
        agent_registry=agent_registry,
        memory_service=memory_service,
    )
    context = ToolCallContext(
        agent=_orchestrator_agent(),
        session_id="session-1",
        trace_id="run-1:analyze-requirements",
    )

    await registry.execute(
        agent_id="genie-orchestrator",
        tool_name="call_requirements_analyst",
        arguments={"transcript_excerpt": "We need a chatbot.", "user_message": ""},
        context=context,
    )

    [write] = memory_service.shared.writes
    assert write["agent"].id == "requirements-analyst"
    assert write["session_id"] == "session-1"
    assert write["trace_id"] == "run-1:analyze-requirements"
    assert write["key"] == "analyze-requirements"
    assert write["classification"] == "requirement"
    assert write["content"] == {"output_text": "Extracted requirements."}
    assert write["approval_status"] == "approved"


async def test_delegation_skips_shared_memory_write_without_a_resolvable_step_id():
    registry = AgentToolRegistry()
    gateway = _RecordingAgentGateway(output_text="Extracted requirements.")
    governance_service = _RecordingGovernanceService()
    memory_service = _FakeMemoryService()
    agent_registry = _FakeAgentRegistry({"requirements-analyst": _requirements_analyst_agent()})
    register_orchestrator_delegation_tools(
        registry,
        agent_gateway=gateway,
        governance_service=governance_service,
        agent_registry=agent_registry,
        memory_service=memory_service,
    )
    context = ToolCallContext(agent=_orchestrator_agent(), session_id="session-1", trace_id="trace-1")

    await registry.execute(
        agent_id="genie-orchestrator",
        tool_name="call_requirements_analyst",
        arguments={"transcript_excerpt": "x", "user_message": ""},
        context=context,
    )

    assert memory_service.shared.writes == []
