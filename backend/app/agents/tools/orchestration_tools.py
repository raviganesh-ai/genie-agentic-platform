"""Genie Orchestrator delegation function tools.

``genie-orchestrator`` drives each mission phase itself: rather than a
fixed declarative workflow step invoking one specialist agent directly,
each ``solution-discovery-workflow`` step now invokes ``genie-orchestrator``
(see ``config/workflows/registry.yaml``), which calls exactly the
delegation tool registered here for that phase (restricted per step via
``WorkflowStep.allowed_tool_names``/``AgentExecutionRequest.allowed_tool_names``)
to actually execute the real specialist agent.

Azure AI Foundry's SDK (``azure-ai-projects`` 2.3.x, the version this
platform is pinned to) exposes no "Connected Agents" tool class for Prompt
Agents (confirmed by inspecting ``azure.ai.projects.models`` directly - see
session notes); the previously assumed mechanism does not exist in this
SDK version. Delegation is therefore implemented the same way every other
agent tool in this codebase is: a genuine ``AgentToolRegistry`` function
tool, dispatched when the Foundry run reaches a tool call, whose real
implementation executes the target specialist through the very same
``AgentGateway.execute`` path (``AzureAgentGateway`` in production) used
everywhere else in Genie - never a canned or synthesized response. Each
delegated call is recorded as its own governance execution event (the same
``GovernanceService.record_execution`` call ``WorkflowStepExecutor`` makes
for every other step), so the real per-agent call order stays fully
traceable (e.g. for the Triage panel) even though it is now nested inside
a single ``genie-orchestrator`` run.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.agents.gateway import AgentGateway, get_enabled_agent
from app.agents.models import AgentExecutionRequest, AgentExecutionResult
from app.agents.registry import AgentRegistry
from app.agents.tool_execution import AgentToolRegistry, ToolCallContext, ToolExecutionError
from app.agents.tools.architecture_parsing import parse_architecture_build_plan
from app.governance.governance_service import GovernanceService
from app.memory.memory_models import SharedMemoryClassification
from app.memory.memory_service import MemoryService
from app.models.workflow_stream_models import WorkflowStreamEvent
from app.orchestration.workflow_event_bus import WorkflowEventBus

__all__ = ["register_orchestrator_delegation_tools", "resolve_delegate_agent_id"]

_ORCHESTRATOR_AGENT_ID = "genie-orchestrator"
_PREVIEW_MAX_LENGTH = 240
_BUILD_AGENT_TOOL_NAME = "call_build_agent"
_BUILD_COMPONENT_PROMPT_ID = "build-generation-component-v1"

# Marks a component's placeholder piece (see _component_failure_piece) as a
# genuine failure, never reusable code - _extract_reusable_components always
# excludes a block carrying this marker, so a retry always regenerates it.
_COMPONENT_FAILURE_MARKER = "GENERATION FAILED"

# Matches one component's whole fenced code block by its own first-line
# ``# agent: <name>``/``// agent: <name>`` label comment (the same
# convention build-generation-component-v1 requires every component to
# follow, and the same one frontend/src/utils/textArtifacts.ts's
# AGENT_LABEL_COMMENT recognizes) - used to find already-succeeded
# components inside a prior attempt's full output text.
_AGENT_LABEL_BLOCK_PATTERN = re.compile(
    r"```[a-zA-Z0-9_-]*\n(?:#|//)\s*agent:\s*(?P<name>[^\n]+?)\s*\n[\s\S]*?```"
)


def _preview(output_text: str | None) -> str | None:
    if not output_text:
        return None
    flattened = " ".join(output_text.split())
    if len(flattened) <= _PREVIEW_MAX_LENGTH:
        return flattened
    return f"{flattened[:_PREVIEW_MAX_LENGTH]}..."


def _parse_excluded_agent_names(excluded_agents: str) -> frozenset[str]:
    """Normalizes the comma-separated ``excluded_agents`` variable (the
    names the user deselected on Architecture Studio's "Multi-Agent
    Workflow" checklist - see ``ArchitectureStudioPage.tsx``) into a
    case/whitespace-insensitive set, so it can be matched against
    ``ArchitectureBuildPlan.specialist_agent_names`` regardless of minor
    formatting differences.
    """

    return frozenset(
        name.strip().lower() for name in excluded_agents.split(",") if name.strip()
    )


def _split_trace_id(trace_id: str) -> tuple[str | None, str | None]:
    """Recovers the ``(workflow_run_id, step_id)`` a delegated call belongs to.

    ``genie-orchestrator``'s delegation tools are only ever invoked from
    within a running workflow step, whose ``WorkflowStepExecutor.execute_step``
    call always sets ``correlation_id`` to ``f"{workflow_run_id}:{step.id}"``
    (``app.orchestration.workflow_runtime``) - that same value is threaded
    through to ``ToolCallContext.trace_id`` unchanged
    (``AzureAgentGateway.execute``). Recovering it here lets the delegated
    specialist's own governance/live-stream events carry the same
    ``step_id``/``workflow_run_id`` as the orchestrator's, so a live
    consumer (the Triage panel, the workflow-events SSE stream) can group
    both together as one step's real control flow instead of two unrelated
    entries.
    """

    workflow_run_id, separator, step_id = trace_id.partition(":")
    return (workflow_run_id, step_id) if separator else (None, None)


@dataclass(frozen=True)
class _Delegation:
    """One connected specialist genie-orchestrator may delegate a phase to."""

    tool_name: str
    target_agent_id: str
    target_prompt_id: str
    variable_names: tuple[str, ...]
    shared_memory_classification: SharedMemoryClassification


# Mirrors config/agents/registry.yaml's genie-orchestrator.connected_agent_ids
# and its tool_definitions (parameter names match each target agent's own
# registered prompt template variables 1:1) - never business/customer data,
# purely structural wiring between two already-externally-configured ids.
# ``shared_memory_classification`` likewise just categorizes *what kind* of
# artifact each specialist produces for Shared Collaboration Memory (see
# ``SharedMemoryClassification``) - not any customer content itself.
_DELEGATIONS: tuple[_Delegation, ...] = (
    _Delegation(
        tool_name="call_requirements_analyst",
        target_agent_id="requirements-analyst",
        target_prompt_id="requirements-extraction-v1",
        variable_names=("transcript_excerpt", "user_message"),
        shared_memory_classification="requirement",
    ),
    _Delegation(
        tool_name="call_architecture_designer",
        target_agent_id="architecture-designer",
        target_prompt_id="architecture-recommendation-v1",
        variable_names=("approved_requirements", "user_message"),
        shared_memory_classification="architecture_finding",
    ),
    _Delegation(
        tool_name="call_build_agent",
        target_agent_id="build-agent",
        target_prompt_id="build-generation-v1",
        variable_names=(
            "requirements",
            "architecture",
            "policies",
            "excluded_agents",
            "user_message",
            # Sourced from this same step's own prior attempt output (see
            # config/workflows/registry.yaml's self-referential
            # "previous_build_output: step:build-solution") - always present
            # (as genie-orchestrator's own resolved variable, in
            # context.variables) on a retried run, empty on a first
            # attempt. Never surfaced to the model as a tool-call argument
            # it needs to supply - see _delegate's caller_value precedence.
            "previous_build_output",
        ),
        shared_memory_classification="roadmap_artifact",
    ),
    _Delegation(
        tool_name="call_security_assessment_agent",
        target_agent_id="security-assessment-agent",
        target_prompt_id="security-assessment-v1",
        variable_names=("artifact", "user_message"),
        shared_memory_classification="risk",
    ),
    _Delegation(
        tool_name="call_test_generation_agent",
        target_agent_id="test-generation-agent",
        target_prompt_id="test-generation-v1",
        variable_names=("artifact", "requirements", "user_message"),
        shared_memory_classification="roadmap_artifact",
    ),
    _Delegation(
        tool_name="call_peer_review_agent",
        target_agent_id="peer-review-agent",
        target_prompt_id="peer-review-v1",
        variable_names=("artifact", "requirements", "policies", "security_findings", "test_plan", "user_message"),
        shared_memory_classification="approval",
    ),
)


def resolve_delegate_agent_id(tool_name: str) -> str | None:
    """Returns the specialist agent id ``tool_name`` delegates to, if any.

    Every ``solution-discovery-workflow`` step's own ``WorkflowStepResult``
    is recorded under ``genie-orchestrator``'s agent id (the step's
    configured ``agent_id`` - see ``WorkflowStepExecutor.execute_step``),
    never the real specialist that produced the content. Callers that need
    to know *which specialist actually produced* a given step's output
    (e.g. ``ArchitectureService``) can recover it from that step's
    ``allowed_tool_names`` via this lookup instead.
    """

    for delegation in _DELEGATIONS:
        if delegation.tool_name == tool_name:
            return delegation.target_agent_id
    return None


def register_orchestrator_delegation_tools(
    registry: AgentToolRegistry,
    *,
    agent_gateway: AgentGateway,
    governance_service: GovernanceService,
    agent_registry: AgentRegistry | None = None,
    memory_service: MemoryService | None = None,
    event_bus: WorkflowEventBus | None = None,
) -> None:
    """Register every ``call_<agent>`` delegation tool for ``genie-orchestrator``.

    Must be called after ``agent_gateway`` (typically ``AzureAgentGateway``)
    has been constructed, but populates the same ``AgentToolRegistry``
    instance that was already handed to that gateway's ``FoundryAgentProvider``
    - lookups happen lazily at call time, so this ordering is safe and
    avoids a real circular construction dependency between the gateway and
    its own tool registry.

    ``agent_registry``/``memory_service``, when supplied, make each delegated
    call write the specialist's own real output into Shared Collaboration
    Memory (keyed by the workflow step id) immediately after it returns -
    this is what lets a downstream step's agent (e.g. architecture-designer
    reading requirements-analyst's finding) genuinely read a prior
    specialist's output back out of Shared Memory rather than relying only
    on the orchestrator's own in-process step output. Omit either to skip
    this (e.g. tests that do not exercise memory).

    ``event_bus``, when supplied, makes each delegated call stream the
    target specialist's own real Foundry completion (via
    ``AgentGateway.execute_stream``) and publish one ``step_delta`` event
    per incremental chunk - tagged with that specialist's own ``agent_id``
    (e.g. ``"build-agent"``), never ``genie-orchestrator``'s - instead of
    only calling the blocking ``execute``. Without this, a live SSE
    consumer sees nothing at all for the delegated call's entire real
    duration (which can be several minutes for a large generation such as
    ``build-generation-v1``): genie-orchestrator's own run is paused on
    ``requires_action`` the whole time, so its own ``step_started``/
    ``step_delta``/``step_completed`` events only resume once this
    (previously non-streamed) delegated call had already returned in full.
    """

    for delegation in _DELEGATIONS:
        registry.register(
            agent_id=_ORCHESTRATOR_AGENT_ID,
            tool_name=delegation.tool_name,
            fn=_build_delegation_tool(
                delegation,
                agent_gateway=agent_gateway,
                governance_service=governance_service,
                agent_registry=agent_registry,
                memory_service=memory_service,
                event_bus=event_bus,
            ),
        )


async def _stream_and_publish_deltas(
    agent_gateway: AgentGateway,
    request: AgentExecutionRequest,
    event_bus: WorkflowEventBus,
    *,
    session_id: str,
    workflow_run_id: str,
    step_id: str,
    agent_id: str,
) -> AgentExecutionResult:
    """Streams a delegated specialist's own real completion via ``execute_stream``.

    Publishes one ``step_delta`` event per incremental chunk the specialist
    itself produces, tagged with ``agent_id`` (the specialist's own id, e.g.
    ``"build-agent"`` - never ``genie-orchestrator``'s), so a live SSE
    consumer (``useWorkflowEventStream`` on the Workshop page) can render
    this specialist's real output as it is actually generated - e.g. the
    Build Agent's multi-file code generation, which can take several
    minutes - rather than only seeing it once genie-orchestrator's own run
    finishes echoing the tool's full result back afterward. Tagging by the
    specialist's own ``agent_id`` (distinct from genie-orchestrator's own
    ``step_delta`` events for the same ``step_id``) lets a consumer
    distinguish this real, first-generated content from genie-orchestrator's
    own later, purely-repeated echo of the same text.
    """

    result: AgentExecutionResult | None = None
    async for chunk in agent_gateway.execute_stream(request):
        if chunk.delta:
            await event_bus.publish(
                WorkflowStreamEvent(
                    event_type="step_delta",
                    session_id=session_id,
                    workflow_run_id=workflow_run_id,
                    step_id=step_id,
                    agent_id=agent_id,
                    delta=chunk.delta,
                )
            )
        if chunk.result is not None:
            result = chunk.result
    if result is None:
        raise ToolExecutionError(
            f"Agent gateway stream for delegated agent '{agent_id}' ended without a final result."
        )
    return result


def _extract_reusable_components(previous_build_output: str) -> dict[str, str]:
    """Maps each component name (lowercased) that fully succeeded on a
    prior ``build-solution`` attempt to its own previously generated
    fenced code block.

    Recognizes each block the same way ``build-generation-component-v1``
    requires every component to identify itself: a fenced code block whose
    first line is a ``# agent: <name>``/``// agent: <name>`` comment (see
    ``_AGENT_LABEL_BLOCK_PATTERN``). A block carrying
    ``_COMPONENT_FAILURE_MARKER`` (see ``_component_failure_piece``) is
    deliberately excluded - it represents a component that failed last
    time and must always be regenerated, never mistaken for reusable code.

    Called with ``previous_build_output`` (see the self-referential
    ``previous_build_output: step:build-solution`` variable source in
    ``config/workflows/registry.yaml``) so a retried attempt can reuse
    already-succeeded components verbatim instead of unconditionally
    regenerating the entire build from scratch - directly addressing the
    "all or nothing" retry behavior a single component failure used to
    cause.
    """

    reusable: dict[str, str] = {}
    for match in _AGENT_LABEL_BLOCK_PATTERN.finditer(previous_build_output or ""):
        block_text = match.group(0)
        if _COMPONENT_FAILURE_MARKER in block_text:
            continue
        reusable[match.group("name").strip().lower()] = block_text
    return reusable


def _component_failure_piece(component_name: str, *, is_ui: bool, exc: BaseException) -> str:
    """Renders one component's generation failure as its own clearly
    labeled placeholder instead of letting the exception abort the whole
    ``build-solution`` step and silently lose every OTHER component that
    would otherwise have succeeded.

    Follows the same ``# agent: <name>``/``// agent: <name>`` first-line
    convention every successfully generated component uses, so the
    frontend's generic code-block extraction (``extractCodeBlocks``/
    ``AGENT_LABEL_COMMENT`` in ``frontend/src/utils/textArtifacts.ts``,
    which matches any fence language tag) still renders this as its own,
    clearly attributed artifact card - never buried inside a single
    generic "Multi-Agent Workflow Design" narrative fallback covering the
    whole response, which is what happened when the very first component
    call failed and aborted every later one. Marked with
    ``_COMPONENT_FAILURE_MARKER`` so a later retry's
    ``_extract_reusable_components`` always regenerates this component
    again.
    """

    comment_prefix = "//" if is_ui else "#"
    return (
        f"```text\n"
        f"{comment_prefix} agent: {component_name}\n"
        f"{comment_prefix} {_COMPONENT_FAILURE_MARKER}: {exc}\n"
        f"```"
    )


async def _generate_build_by_component(
    *,
    delegation: _Delegation,
    base_variables: dict[str, str],
    agent_gateway: AgentGateway,
    context: ToolCallContext,
    event_bus: WorkflowEventBus | None,
    workflow_run_id: str | None,
    step_id: str | None,
) -> AgentExecutionResult:
    """Generates the ``build-solution`` step's code one component at a time
    (each specialist agent, then the Orchestrator Agent, then the UI)
    instead of one long combined completion, so the Workshop page streams
    each component's own real code as soon as it is done - see
    ``build-generation-component-v1`` and ``parse_architecture_build_plan``.

    Any specialist agent named in ``base_variables["excluded_agents"]``
    (the user's deselections on Architecture Studio's "Multi-Agent
    Workflow" checklist - see ``ArchitectureStudioPage.tsx``) is skipped
    entirely here - no component call is ever made for it - and the same
    value is also forwarded to the Orchestrator/UI component calls below
    so their own generated code does not reference it either.

    Falls back to a single combined ``build-generation-v1`` call (the
    prior behavior) whenever the architecture document's own "##
    Multi-Agent Workflow" section does not parse into a clean agent list -
    this split is purely a streaming-UX improvement, never a reason to
    fail the whole build over a structural parsing gap.

    Each component's generation is isolated: one component's failure
    renders as its own placeholder (``_component_failure_piece``) instead
    of propagating and aborting every other, still-generatable component.
    Any component that already succeeded on a prior attempt at this same
    step (``base_variables["previous_build_output"]`` -
    ``_extract_reusable_components``) is reused verbatim rather than
    regenerated - so retrying (e.g. "Re-run UI & Agent Design") only ever
    (re)generates the component(s) that actually still need it, never the
    whole build from scratch.
    """

    plan = parse_architecture_build_plan(base_variables.get("architecture", ""))
    if plan is None:
        request = AgentExecutionRequest(
            agent_id=delegation.target_agent_id,
            prompt_id=delegation.target_prompt_id,
            variables=base_variables,
            correlation_id=context.trace_id,
            session_id=context.session_id,
        )
        if event_bus is not None and workflow_run_id is not None and step_id is not None:
            return await _stream_and_publish_deltas(
                agent_gateway,
                request,
                event_bus,
                session_id=context.session_id,
                workflow_run_id=workflow_run_id,
                step_id=step_id,
                agent_id=delegation.target_agent_id,
            )
        return await agent_gateway.execute(request)

    components: list[tuple[str, str]] = [
        ("agent", name)
        for name in plan.specialist_agent_names
        if name.strip().lower() not in _parse_excluded_agent_names(base_variables.get("excluded_agents", ""))
    ]
    components.append(("orchestrator", plan.orchestrator_agent_name))
    components.append(("ui", "ui"))

    reusable_components = _extract_reusable_components(base_variables.get("previous_build_output", ""))

    async def _publish_delta(delta: str) -> None:
        if event_bus is not None and workflow_run_id is not None and step_id is not None:
            await event_bus.publish(
                WorkflowStreamEvent(
                    event_type="step_delta",
                    session_id=context.session_id,
                    workflow_run_id=workflow_run_id,
                    step_id=step_id,
                    agent_id=delegation.target_agent_id,
                    delta=delta,
                )
            )

    pieces: list[str] = []
    for index, (component_kind, component_name) in enumerate(components):
        if index > 0:
            # A blank-line separator between components' own streamed
            # text, so consecutive fenced code blocks never glue together
            # with no whitespace between them - matches how the final
            # concatenation below joins each piece.
            await _publish_delta("\n\n")

        # build-generation-component-v1 contracts the orchestrator/UI
        # components to always self-label with the literal "orchestrator"/
        # "ui" (never the orchestrator's own real display name) - see that
        # prompt template - so both the reuse lookup below and any failure
        # placeholder (_component_failure_piece) must use the same literal
        # label, not component_name, for those two kinds.
        label_name = component_name if component_kind == "agent" else component_kind
        reused_piece = reusable_components.get(label_name.strip().lower())
        if reused_piece is not None:
            # Already succeeded on a prior attempt at this same step - reuse
            # its real code verbatim (see _extract_reusable_components)
            # instead of paying for, and risking another failure on, a
            # brand new agent call for a component that already has valid
            # output.
            await _publish_delta(reused_piece)
            pieces.append(reused_piece)
            continue

        component_variables = {
            **base_variables,
            "component_kind": component_kind,
            "component_name": component_name,
        }
        request = AgentExecutionRequest(
            agent_id=delegation.target_agent_id,
            prompt_id=_BUILD_COMPONENT_PROMPT_ID,
            variables=component_variables,
            correlation_id=context.trace_id,
            session_id=context.session_id,
        )
        try:
            if event_bus is not None and workflow_run_id is not None and step_id is not None:
                component_result = await _stream_and_publish_deltas(
                    agent_gateway,
                    request,
                    event_bus,
                    session_id=context.session_id,
                    workflow_run_id=workflow_run_id,
                    step_id=step_id,
                    agent_id=delegation.target_agent_id,
                )
            else:
                component_result = await agent_gateway.execute(request)
        except Exception as exc:  # noqa: BLE001 - isolate this ONE component's
            # failure (a transient Foundry/tool error, ...) so every OTHER,
            # still-generatable component is not silently discarded along
            # with it (the "all or nothing" behavior this replaces) - see
            # _component_failure_piece and the module docstring.
            failure_piece = _component_failure_piece(
                label_name, is_ui=component_kind == "ui", exc=exc
            )
            await _publish_delta(failure_piece)
            pieces.append(failure_piece)
            continue
        pieces.append(component_result.output_text)

    return AgentExecutionResult(
        agent_id=delegation.target_agent_id,
        output_text="\n\n".join(pieces),
        correlation_id=context.trace_id,
    )


def _build_delegation_tool(
    delegation: _Delegation,
    *,
    agent_gateway: AgentGateway,
    governance_service: GovernanceService,
    agent_registry: AgentRegistry | None,
    memory_service: MemoryService | None,
    event_bus: WorkflowEventBus | None = None,
):
    async def _delegate(arguments: dict[str, Any], context: ToolCallContext) -> dict[str, Any]:
        if context.session_id is None:
            raise ToolExecutionError(f"'{delegation.tool_name}' requires an active session_id.")

        variables: dict[str, str] = {}
        for name in delegation.variable_names:
            # Prefer genie-orchestrator's own resolved variable value (the
            # exact same upstream step output/transcript text that was
            # substituted into its own prompt - see ToolCallContext.variables)
            # over whatever the model echoed back as a tool-call argument.
            # Large text blocks (a full requirements write-up, an
            # architecture recommendation, ...) are not reliably copied
            # verbatim by the model into function-call arguments - it may
            # paraphrase, truncate, or omit them - which would silently
            # break the handoff to the delegated specialist. This is the
            # authoritative source: config/workflows/registry.yaml's
            # variable_sources for this same step always populates it 1:1
            # with genie-orchestrator's own prompt variables. Only fall
            # back to the model-supplied argument when the caller's own
            # variables genuinely have nothing for this name.
            caller_value = context.variables.get(name)
            if caller_value is not None:
                variables[name] = caller_value
                continue

            value = arguments.get(name, "")
            if not isinstance(value, str):
                raise ToolExecutionError(
                    f"'{delegation.tool_name}' requires '{name}' to be a string."
                )
            variables[name] = value

        request = AgentExecutionRequest(
            agent_id=delegation.target_agent_id,
            prompt_id=delegation.target_prompt_id,
            variables=variables,
            correlation_id=context.trace_id,
            session_id=context.session_id,
        )
        workflow_run_id, step_id = _split_trace_id(context.trace_id)
        if delegation.tool_name == _BUILD_AGENT_TOOL_NAME:
            result = await _generate_build_by_component(
                delegation=delegation,
                base_variables=variables,
                agent_gateway=agent_gateway,
                context=context,
                event_bus=event_bus,
                workflow_run_id=workflow_run_id,
                step_id=step_id,
            )
        elif event_bus is not None and workflow_run_id is not None and step_id is not None:
            result = await _stream_and_publish_deltas(
                agent_gateway,
                request,
                event_bus,
                session_id=context.session_id,
                workflow_run_id=workflow_run_id,
                step_id=step_id,
                agent_id=delegation.target_agent_id,
            )
        else:
            result = await agent_gateway.execute(request)

        await governance_service.record_execution(
            session_id=context.session_id,
            trace_id=context.trace_id,
            agent_id=delegation.target_agent_id,
            detail={
                "delegated_by": _ORCHESTRATOR_AGENT_ID,
                "step_id": step_id,
                "output_preview": _preview(result.output_text),
            },
        )

        if memory_service is not None and agent_registry is not None and step_id is not None:
            target_agent = get_enabled_agent(agent_registry, delegation.target_agent_id)
            # approval_status is always "approved" here rather than plumbing
            # through a real human approval decision: this write only ever
            # represents the specialist's own freshly generated artifact for
            # its own step, so an "overwrite" only happens when the workflow
            # itself intentionally re-runs that step (e.g. a customer chat
            # follow-up) - a legitimate system-driven regeneration, not an
            # unreviewed agent silently clobbering approved shared content.
            await memory_service.shared.write(
                agent=target_agent,
                session_id=context.session_id,
                trace_id=context.trace_id,
                key=step_id,
                classification=delegation.shared_memory_classification,
                content={"output_text": result.output_text or ""},
                approval_status="approved",
            )

        return {"output_text": result.output_text}

    return _delegate
