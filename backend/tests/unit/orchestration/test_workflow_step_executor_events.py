"""Unit tests for `WorkflowStepExecutor`'s live event agent-id attribution.

Every `solution-discovery-workflow` step's own configured `agent_id` is
`"genie-orchestrator"` (see `config/workflows/registry.yaml` - the
orchestrator delegates the real work via its one allowed
`call_<specialist>` tool). Live `step_started`/`step_delta`/
`step_completed`/`step_failed` events published to `WorkflowEventBus` (and
shown to users e.g. via `LiveWorkflowPulse`) must attribute to that real
specialist - never the orchestrator itself - matching the delegated tool
call's own `step_delta` events (see
`orchestration_tools._stream_and_publish_deltas`)."""
from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from app.agents.foundry.errors import FoundryUnavailableError
from app.agents.models import (
    AgentDefinition,
    AgentExecutionRequest,
    AgentExecutionResult,
    AgentExecutionStreamChunk,
)
from app.models.workflow_stream_models import WorkflowStreamEvent
from app.orchestration.workflow_step_executor import (
    WorkflowStepExecutor,
    _display_agent_id,
    _require_complete_requirement_coverage,
)
from app.workflows.models import WorkflowStep


def _step(allowed_tool_names: list[str] | None) -> WorkflowStep:
    return WorkflowStep(
        id="design-architecture",
        agent_id="genie-orchestrator",
        description="Design the architecture.",
        allowed_tool_names=allowed_tool_names,
    )


def test_display_agent_id_resolves_the_real_specialist_from_the_delegation_tool() -> None:
    step = _step(["call_architecture_designer"])

    assert _display_agent_id(step, "genie-orchestrator") == "architecture-designer"


def test_display_agent_id_falls_back_when_no_tool_names_are_configured() -> None:
    step = _step(None)

    assert _display_agent_id(step, "genie-orchestrator") == "genie-orchestrator"


def test_display_agent_id_falls_back_when_no_tool_name_resolves_to_a_delegation() -> None:
    step = _step(["not_a_delegation_tool"])

    assert _display_agent_id(step, "genie-orchestrator") == "genie-orchestrator"


def test_architecture_coverage_fails_closed_when_one_approved_id_is_omitted() -> None:
    with pytest.raises(FoundryUnavailableError, match="REQ-002"):
        _require_complete_requirement_coverage(
            step_id="design-architecture",
            variables={"approved_requirements": "[REQ-001] Search. [REQ-002] Export."},
            output_text="Search Agent covers REQ-001.",
        )


def test_build_coverage_accepts_every_approved_id_across_generated_components() -> None:
    _require_complete_requirement_coverage(
        step_id="build-solution",
        variables={"requirements": "[REQ-001] Search. [REQ-002] Export."},
        output_text="# requirements: REQ-001\n// requirements: REQ-002",
    )


def test_build_coverage_rejects_failed_component_placeholder() -> None:
    with pytest.raises(FoundryUnavailableError, match="placeholder artifacts"):
        _require_complete_requirement_coverage(
            step_id="build-solution",
            variables={"requirements": "[REQ-001] Search."},
            output_text="# requirements: REQ-001\n# GENERATION FAILED: Foundry unavailable",
        )


class _StreamingGateway:
    """Replays a fixed sequence of chunks for `execute_stream`, regardless
    of the request - stands in for genie-orchestrator's own Foundry run,
    whose final chunks are its own completion (see the module docstring
    tests below)."""

    def __init__(self, chunks: list[AgentExecutionStreamChunk]) -> None:
        self._chunks = chunks

    async def execute_stream(
        self, request: AgentExecutionRequest
    ) -> AsyncIterator[AgentExecutionStreamChunk]:
        for chunk in self._chunks:
            yield chunk


class _RecordingEventBus:
    def __init__(self) -> None:
        self.events: list[WorkflowStreamEvent] = []

    async def publish(self, event: WorkflowStreamEvent) -> None:
        self.events.append(event)


def _orchestrator_agent() -> AgentDefinition:
    return AgentDefinition(
        id="genie-orchestrator",
        name="Genie Orchestrator",
        role="mission_orchestration",
        description="Drives the mission.",
    )


def _request(step_id: str) -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agent_id="genie-orchestrator",
        prompt_id="orchestrator-build-phase-v1",
        variables={},
        correlation_id=f"run-1:{step_id}",
        session_id="session-1",
    )


async def test_run_agent_suppresses_its_own_step_delta_echo_for_a_delegated_step() -> None:
    """The orchestrator-*-phase-v1 prompts instruct genie-orchestrator to,
    once its delegated tool call returns, "respond with EXACTLY that
    tool's returned output text and nothing else" - so this outer
    completion is always a verbatim re-typing of content the delegated
    tool call already published its own step_delta events for (tagged
    with this same display_agent_id/step_id). Publishing it again here
    would make a live consumer see the whole already-finished generation
    appear to restart right after it just completed."""

    event_bus = _RecordingEventBus()
    gateway = _StreamingGateway(
        [
            AgentExecutionStreamChunk(delta="echoed "),
            AgentExecutionStreamChunk(delta="text"),
            AgentExecutionStreamChunk(
                result=AgentExecutionResult(
                    agent_id="genie-orchestrator",
                    output_text="echoed text",
                    correlation_id="run-1:build-solution",
                )
            ),
        ]
    )
    executor = WorkflowStepExecutor(
        agent_registry=None,
        prompt_registry=None,
        agent_gateway=gateway,
        governance_service=None,
        event_bus=event_bus,
    )
    step = WorkflowStep(
        id="build-solution",
        agent_id="genie-orchestrator",
        description="Build the solution.",
        allowed_tool_names=["call_build_agent"],
    )

    result = await executor._run_agent(
        request=_request("build-solution"),
        session_id="session-1",
        workflow_run_id="run-1",
        step=step,
        agent=_orchestrator_agent(),
    )

    assert result.output_text == "echoed text"
    event_types = [event.event_type for event in event_bus.events]
    assert event_types == ["step_started", "step_completed"]
    assert all(event.agent_id == "build-agent" for event in event_bus.events)
    # A delegated step's own step_completed must never preview
    # genie-orchestrator's raw output_text - it is always either a verbatim
    # echo already shown via the delegated call's own step_delta events, or
    # (see the dedicated marker test below) the internal delegation
    # sentinel, neither of which belongs in a user-facing activity banner.
    assert event_bus.events[-1].output_preview is None


async def test_run_agent_suppresses_the_delegation_marker_from_a_delegated_steps_completed_preview() -> (
    None
):
    """When the specialist's real output was too large to inline and was
    stored to shared memory instead, genie-orchestrator's own raw
    output_text is literally the internal `_DELEGATED_OUTPUT_MARKER`
    sentinel (`"DELEGATED_OUTPUT_STORED"`) - this must never leak into the
    step_completed event's preview shown to users."""

    event_bus = _RecordingEventBus()
    gateway = _StreamingGateway(
        [
            AgentExecutionStreamChunk(
                result=AgentExecutionResult(
                    agent_id="genie-orchestrator",
                    output_text="DELEGATED_OUTPUT_STORED",
                    correlation_id="run-1:design-architecture",
                )
            ),
        ]
    )
    executor = WorkflowStepExecutor(
        agent_registry=None,
        prompt_registry=None,
        agent_gateway=gateway,
        governance_service=None,
        event_bus=event_bus,
    )
    step = _step(["call_architecture_designer"])

    await executor._run_agent(
        request=_request("design-architecture"),
        session_id="session-1",
        workflow_run_id="run-1",
        step=step,
        agent=_orchestrator_agent(),
    )

    completed_event = next(e for e in event_bus.events if e.event_type == "step_completed")
    assert completed_event.output_preview is None


async def test_run_agent_still_publishes_step_delta_for_a_non_delegated_step() -> None:
    """A step with no delegation tool (display_agent_id falls back to the
    step's own configured agent) has no other publisher for its content -
    genie-orchestrator's own stream remains the sole source and must keep
    streaming live, as before."""

    event_bus = _RecordingEventBus()
    gateway = _StreamingGateway(
        [
            AgentExecutionStreamChunk(delta="chunk-1"),
            AgentExecutionStreamChunk(
                result=AgentExecutionResult(
                    agent_id="genie-orchestrator",
                    output_text="chunk-1",
                    correlation_id="run-1:mission-kickoff",
                )
            ),
        ]
    )
    executor = WorkflowStepExecutor(
        agent_registry=None,
        prompt_registry=None,
        agent_gateway=gateway,
        governance_service=None,
        event_bus=event_bus,
    )
    step = WorkflowStep(
        id="mission-kickoff",
        agent_id="genie-orchestrator",
        description="No delegation configured for this step.",
    )

    await executor._run_agent(
        request=_request("mission-kickoff"),
        session_id="session-1",
        workflow_run_id="run-1",
        step=step,
        agent=_orchestrator_agent(),
    )

    event_types = [event.event_type for event in event_bus.events]
    assert event_types == ["step_started", "step_delta", "step_completed"]
    # Non-delegated steps have no other publisher for their content, so
    # their own step_completed preview is still shown.
    assert event_bus.events[-1].output_preview == "chunk-1"
