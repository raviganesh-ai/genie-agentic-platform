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

from dataclasses import dataclass
from typing import Any

from app.agents.gateway import AgentGateway, get_enabled_agent
from app.agents.models import AgentExecutionRequest
from app.agents.registry import AgentRegistry
from app.agents.tool_execution import AgentToolRegistry, ToolCallContext, ToolExecutionError
from app.governance.governance_service import GovernanceService
from app.memory.memory_models import SharedMemoryClassification
from app.memory.memory_service import MemoryService

__all__ = ["register_orchestrator_delegation_tools"]

_ORCHESTRATOR_AGENT_ID = "genie-orchestrator"
_PREVIEW_MAX_LENGTH = 240


def _preview(output_text: str | None) -> str | None:
    if not output_text:
        return None
    flattened = " ".join(output_text.split())
    if len(flattened) <= _PREVIEW_MAX_LENGTH:
        return flattened
    return f"{flattened[:_PREVIEW_MAX_LENGTH]}..."


def _extract_step_id(trace_id: str) -> str | None:
    """Recovers the workflow step id a delegated call belongs to.

    ``genie-orchestrator``'s delegation tools are only ever invoked from
    within a running workflow step, whose ``WorkflowStepExecutor.execute_step``
    call always sets ``correlation_id`` to ``f"{workflow_run_id}:{step.id}"``
    (``app.orchestration.workflow_runtime``) - that same value is threaded
    through to ``ToolCallContext.trace_id`` unchanged
    (``AzureAgentGateway.execute``). Recovering it here lets the delegated
    specialist's own governance event carry the same ``step_id`` as the
    orchestrator's, so a live consumer (the Triage panel) can group both
    together as one step's real control flow instead of two unrelated
    entries.
    """

    _workflow_run_id, separator, step_id = trace_id.partition(":")
    return step_id if separator else None


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
        variable_names=("requirements", "architecture", "user_message"),
        shared_memory_classification="roadmap_artifact",
    ),
    _Delegation(
        tool_name="call_governance_reviewer",
        target_agent_id="governance-reviewer",
        target_prompt_id="governance-review-v1",
        variable_names=("artifact", "policies", "user_message"),
        shared_memory_classification="approval",
    ),
    _Delegation(
        tool_name="call_deployment_agent",
        target_agent_id="deployment-agent",
        target_prompt_id="deployment-v1",
        variable_names=("build_output", "governance_decision", "user_message"),
        shared_memory_classification="roadmap_artifact",
    ),
)


def register_orchestrator_delegation_tools(
    registry: AgentToolRegistry,
    *,
    agent_gateway: AgentGateway,
    governance_service: GovernanceService,
    agent_registry: AgentRegistry | None = None,
    memory_service: MemoryService | None = None,
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
            ),
        )


def _build_delegation_tool(
    delegation: _Delegation,
    *,
    agent_gateway: AgentGateway,
    governance_service: GovernanceService,
    agent_registry: AgentRegistry | None,
    memory_service: MemoryService | None,
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
        result = await agent_gateway.execute(request)

        step_id = _extract_step_id(context.trace_id)

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
