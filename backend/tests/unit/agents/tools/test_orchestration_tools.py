"""Unit tests for genie-orchestrator's delegation function tools."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.agents.models import (
    AgentDefinition,
    AgentExecutionRequest,
    AgentExecutionResult,
    AgentExecutionStreamChunk,
)
from app.agents.tool_execution import AgentToolRegistry, ToolCallContext, ToolExecutionError
from app.agents.tools.orchestration_tools import register_orchestrator_delegation_tools
from app.models.workflow_stream_models import WorkflowStreamEvent


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


class _StreamingAgentGateway:
    """Records every request and supports both `execute` and
    `execute_stream`, returning each component's own text based on its
    `component_name` variable (falling back to a shared default text for
    non-component requests, e.g. the single-combined-call fallback path).

    Any component name listed in `failing_components` raises instead of
    returning - used to prove one component's failure never aborts the
    others (see `_generate_build_by_component`'s per-component isolation).
    """

    def __init__(
        self,
        *,
        texts_by_component: dict[str, str] | None = None,
        default_text: str = "x",
        failing_components: frozenset[str] = frozenset(),
    ) -> None:
        self.requests: list[AgentExecutionRequest] = []
        self._texts_by_component = texts_by_component or {}
        self._default_text = default_text
        self._failing_components = failing_components

    def _text_for(self, request: AgentExecutionRequest) -> str:
        component_name = request.variables.get("component_name")
        if component_name is not None:
            return self._texts_by_component.get(component_name, self._default_text)
        return self._default_text

    async def execute(self, request: AgentExecutionRequest) -> AgentExecutionResult:
        self.requests.append(request)
        component_name = request.variables.get("component_name")
        if component_name in self._failing_components:
            raise RuntimeError(f"Simulated failure for component '{component_name}'.")
        return AgentExecutionResult(
            agent_id=request.agent_id,
            output_text=self._text_for(request),
            correlation_id=request.correlation_id,
        )

    async def execute_stream(
        self, request: AgentExecutionRequest
    ) -> AsyncIterator[AgentExecutionStreamChunk]:
        self.requests.append(request)
        component_name = request.variables.get("component_name")
        if component_name in self._failing_components:
            raise RuntimeError(f"Simulated failure for component '{component_name}'.")
        text = self._text_for(request)
        yield AgentExecutionStreamChunk(delta=text)
        yield AgentExecutionStreamChunk(
            result=AgentExecutionResult(
                agent_id=request.agent_id, output_text=text, correlation_id=request.correlation_id
            )
        )


class _RecordingEventBus:
    def __init__(self) -> None:
        self.events: list[WorkflowStreamEvent] = []

    async def publish(self, event: WorkflowStreamEvent) -> None:
        self.events.append(event)


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
        agent_scope_id="model:gpt-5-mini",
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
    assert request.agent_scope_id == "model:gpt-5-mini"

    [event] = governance_service.calls
    assert event["agent_id"] == "requirements-analyst"
    assert event["session_id"] == "session-1"
    assert event["detail"]["step_id"] == "analyze-requirements"
    assert event["detail"]["delegated_by"] == "genie-orchestrator"
    assert event["detail"]["output_preview"] == "Extracted requirements."


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


_ARCHITECTURE_WITH_TWO_SPECIALISTS = """
## UI Design

- **Kickoff Screen** --> **Support Triage Orchestrator Agent**: lets the
  user describe their issue.

## Multi-Agent Workflow

- **Ticket Classifier Agent**: classifies the incoming issue by category.
- **Resolution Drafter Agent**: drafts a resolution based on the category.
- **Support Triage Orchestrator Agent**: the single entry point.
"""

_ARCHITECTURE_WITHOUT_A_PARSEABLE_WORKFLOW_SECTION = """
## UI Design

Some unrelated UI section text with no Multi-Agent Workflow section at all.
"""


def _build_agent() -> AgentDefinition:
    return AgentDefinition(
        id="build-agent",
        name="Build Agent",
        role="build_generation",
        description="Generates the mission's UI and multi-agent workflow code.",
        foundry_agent_id="build-agent",
        memory_access=["shared"],
    )


async def test_call_build_agent_generates_one_component_at_a_time_when_architecture_parses():
    """When the architecture's own "## Multi-Agent Workflow" section parses
    cleanly, call_build_agent must invoke the Build Agent once per
    component (each specialist, then the orchestrator, then the UI) using
    build-generation-component-v1, rather than one single combined call -
    and still record exactly one governance event/memory write for the
    whole build-solution step, combining every component's own output."""

    registry = AgentToolRegistry()
    gateway = _StreamingAgentGateway(
        texts_by_component={
            "Ticket Classifier Agent": "```python\n# agent: Ticket Classifier Agent\nclassifier code\n```",
            "Resolution Drafter Agent": "```python\n# agent: Resolution Drafter Agent\ndrafter code\n```",
            "Support Triage Orchestrator Agent": "```python\n# agent: orchestrator\norchestrator code\n```",
            "ui": "```tsx\n// agent: ui\nui code\n```",
        }
    )
    governance_service = _RecordingGovernanceService()
    memory_service = _FakeMemoryService()
    agent_registry = _FakeAgentRegistry({"build-agent": _build_agent()})
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
        trace_id="run-1:build-solution",
    )

    result = await registry.execute(
        agent_id="genie-orchestrator",
        tool_name="call_build_agent",
        arguments={
            "requirements": "Approved requirements text.",
            "architecture": _ARCHITECTURE_WITH_TWO_SPECIALISTS,
            "policies": "Must use managed identity (no embedded credentials)",
            "user_message": "",
        },
        context=context,
    )

    assert len(gateway.requests) == 4
    assert [r.prompt_id for r in gateway.requests] == ["build-generation-component-v1"] * 4
    assert [
        (r.variables["component_kind"], r.variables["component_name"]) for r in gateway.requests
    ] == [
        ("agent", "Ticket Classifier Agent"),
        ("agent", "Resolution Drafter Agent"),
        ("orchestrator", "Support Triage Orchestrator Agent"),
        ("ui", "ui"),
    ]
    # Every component call still carries the shared build context unchanged,
    # including the governance policy checklist the user selected on
    # Architecture Studio - each generated component must satisfy it, not
    # just the combined build as a whole.
    for r in gateway.requests:
        assert r.variables["requirements"] == "Approved requirements text."
        assert r.variables["architecture"] == _ARCHITECTURE_WITH_TWO_SPECIALISTS
        assert r.variables["policies"] == "Must use managed identity (no embedded credentials)"

    expected_combined = (
        "```python\n# agent: Ticket Classifier Agent\nclassifier code\n```"
        "\n\n"
        "```python\n# agent: Resolution Drafter Agent\ndrafter code\n```"
        "\n\n"
        "```python\n# agent: orchestrator\norchestrator code\n```"
        "\n\n"
        "```tsx\n// agent: ui\nui code\n```"
    )
    assert result == {"output_text": expected_combined}

    [event] = governance_service.calls
    assert event["agent_id"] == "build-agent"
    assert event["detail"]["step_id"] == "build-solution"

    [write] = memory_service.shared.writes
    assert write["key"] == "build-solution"
    assert write["content"] == {"output_text": expected_combined}


async def test_call_build_agent_streams_each_components_own_deltas_via_the_event_bus():
    registry = AgentToolRegistry()
    gateway = _StreamingAgentGateway(
        texts_by_component={
            "Ticket Classifier Agent": "classifier-code",
            "Resolution Drafter Agent": "drafter-code",
            "Support Triage Orchestrator Agent": "orchestrator-code",
            "ui": "ui-code",
        }
    )
    governance_service = _RecordingGovernanceService()
    event_bus = _RecordingEventBus()
    register_orchestrator_delegation_tools(
        registry,
        agent_gateway=gateway,
        governance_service=governance_service,
        event_bus=event_bus,
    )
    context = ToolCallContext(
        agent=_orchestrator_agent(),
        session_id="session-1",
        trace_id="run-1:build-solution",
    )

    await registry.execute(
        agent_id="genie-orchestrator",
        tool_name="call_build_agent",
        arguments={
            "requirements": "Approved requirements text.",
            "architecture": _ARCHITECTURE_WITH_TWO_SPECIALISTS,
            "policies": "",
            "user_message": "",
        },
        context=context,
    )

    deltas = [event.delta for event in event_bus.events if event.event_type == "step_delta"]
    # One delta per component's own real streamed chunk, plus a "\n\n"
    # separator published between each pair of components (3 separators
    # for 4 components) - proving each component streams independently,
    # in order, rather than one single combined stream.
    assert deltas == [
        "classifier-code",
        "\n\n",
        "drafter-code",
        "\n\n",
        "orchestrator-code",
        "\n\n",
        "ui-code",
    ]
    assert all(event.agent_id == "build-agent" for event in event_bus.events)
    assert all(event.step_id == "build-solution" for event in event_bus.events)


async def test_call_build_agent_falls_back_to_a_single_combined_call_when_architecture_does_not_parse():
    registry = AgentToolRegistry()
    gateway = _StreamingAgentGateway(default_text="The whole combined build output.")
    governance_service = _RecordingGovernanceService()
    register_orchestrator_delegation_tools(
        registry, agent_gateway=gateway, governance_service=governance_service
    )
    context = ToolCallContext(
        agent=_orchestrator_agent(),
        session_id="session-1",
        trace_id="run-1:build-solution",
    )

    result = await registry.execute(
        agent_id="genie-orchestrator",
        tool_name="call_build_agent",
        arguments={
            "requirements": "Approved requirements text.",
            "architecture": _ARCHITECTURE_WITHOUT_A_PARSEABLE_WORKFLOW_SECTION,
            "policies": "",
            "user_message": "",
        },
        context=context,
    )

    [request] = gateway.requests
    assert request.prompt_id == "build-generation-v1"
    assert result == {"output_text": "The whole combined build output."}


async def test_call_build_agent_isolates_one_failing_component_instead_of_losing_every_other_one():
    """A single component's failure (e.g. a transient Foundry error) must
    not abort the other, still-generatable components - this is the
    concrete fix for the "all or nothing" behavior where one failure used
    to lose the entire build-solution output. The failed component renders
    as its own clearly labeled placeholder instead."""

    registry = AgentToolRegistry()
    gateway = _StreamingAgentGateway(
        texts_by_component={
            "Ticket Classifier Agent": "```python\n# agent: Ticket Classifier Agent\nclassifier code\n```",
            "Support Triage Orchestrator Agent": "```python\n# agent: orchestrator\norchestrator code\n```",
            "ui": "```tsx\n// agent: ui\nui code\n```",
        },
        failing_components=frozenset({"Resolution Drafter Agent"}),
    )
    governance_service = _RecordingGovernanceService()
    register_orchestrator_delegation_tools(
        registry, agent_gateway=gateway, governance_service=governance_service
    )
    context = ToolCallContext(
        agent=_orchestrator_agent(),
        session_id="session-1",
        trace_id="run-1:build-solution",
    )

    result = await registry.execute(
        agent_id="genie-orchestrator",
        tool_name="call_build_agent",
        arguments={
            "requirements": "Approved requirements text.",
            "architecture": _ARCHITECTURE_WITH_TWO_SPECIALISTS,
            "policies": "",
            "user_message": "",
        },
        context=context,
    )

    # Every OTHER component still generated its own real code - only the
    # one that actually failed is missing/replaced.
    output_text = result["output_text"]
    assert "classifier code" in output_text
    assert "orchestrator code" in output_text
    assert "ui code" in output_text
    assert "GENERATION FAILED" in output_text
    assert "Resolution Drafter Agent" in output_text
    # All 4 components were still attempted (the failure didn't stop the
    # loop from reaching the remaining ones).
    assert len(gateway.requests) == 4


async def test_call_build_agent_reuses_previously_succeeded_components_on_retry():
    """A retried attempt (previous_build_output populated from this same
    step's own prior, partially-failed output - see the self-referential
    variable source in config/workflows/registry.yaml) must reuse every
    component that already succeeded verbatim, never call the agent again
    for it, and only (re)generate the component that actually still needs
    it - the concrete fix for "why can't I restart from where it failed"."""

    previous_output = (
        "```python\n# agent: Ticket Classifier Agent\nclassifier code\n```"
        "\n\n"
        "```text\n# agent: Resolution Drafter Agent\n# GENERATION FAILED: boom\n```"
        "\n\n"
        "```python\n# agent: orchestrator\norchestrator code\n```"
        "\n\n"
        "```tsx\n// agent: ui\nui code\n```"
    )
    registry = AgentToolRegistry()
    gateway = _StreamingAgentGateway(
        texts_by_component={
            "Resolution Drafter Agent": "```python\n# agent: Resolution Drafter Agent\ndrafter code (retry)\n```",
        }
    )
    governance_service = _RecordingGovernanceService()
    register_orchestrator_delegation_tools(
        registry, agent_gateway=gateway, governance_service=governance_service
    )
    context = ToolCallContext(
        agent=_orchestrator_agent(),
        session_id="session-1",
        trace_id="run-1:build-solution",
    )

    result = await registry.execute(
        agent_id="genie-orchestrator",
        tool_name="call_build_agent",
        arguments={
            "requirements": "Approved requirements text.",
            "architecture": _ARCHITECTURE_WITH_TWO_SPECIALISTS,
            "policies": "",
            "user_message": "",
            "previous_build_output": previous_output,
        },
        context=context,
    )

    # Only the previously-failed component was actually (re)generated.
    assert len(gateway.requests) == 1
    assert gateway.requests[0].variables["component_name"] == "Resolution Drafter Agent"

    output_text = result["output_text"]
    assert "classifier code" in output_text
    assert "orchestrator code" in output_text
    assert "ui code" in output_text
    assert "drafter code (retry)" in output_text
    assert "GENERATION FAILED" not in output_text

