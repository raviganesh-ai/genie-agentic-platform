"""Workflow step executor.

Executes exactly one ``WorkflowStep`` by resolving its registered agent and
prompt template, then delegating to the caller-supplied ``AgentGateway``
(``app.agents.gateway``, unmodified - "All agent execution must flow
through AzureAgentGateway"). Contains no business/reasoning logic of its
own: it only resolves configuration, checks fail-closed preconditions, and
records the resulting governance event.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.agents.foundry.errors import FoundryUnavailableError
from app.agents.gateway import AgentGateway, get_enabled_agent, resolve_prompt_text
from app.agents.models import AgentDefinition, AgentExecutionRequest, AgentExecutionResult
from app.agents.registry import AgentRegistry
from app.agents.tools.orchestration_tools import resolve_delegate_agent_id
from app.governance.governance_service import GovernanceService
from app.memory.memory_service import MemoryService
from app.models.workflow_models import WorkflowStepInput, WorkflowStepResult
from app.models.workflow_stream_models import WorkflowStreamEvent
from app.orchestration.workflow_event_bus import WorkflowEventBus
from app.prompts.registry import PromptRegistry
from app.services.model_catalog_service import ModelCatalogService
from app.services.requirement_fidelity_service import missing_requirement_ids
from app.workflows.models import WorkflowStep

__all__ = ["MissingMemoryReferenceError", "MissingPromptError", "WorkflowStepExecutor"]

_PREVIEW_MAX_LENGTH = 240
_DELEGATED_OUTPUT_MARKER = "DELEGATED_OUTPUT_STORED"
_COMPONENT_FAILURE_MARKER = "GENERATION FAILED"
_REQUIREMENT_COVERAGE_VARIABLES = ("approved_requirements", "requirements")
_MODEL_CATALOG_SOURCE = "model-catalog"


def _format_model_catalog(available_models: list[str], default_model: str) -> str:
    """Renders a real Foundry model catalog as prompt-ready text.

    Used to satisfy the ``build-solution`` step's ``available_models``
    variable (see ``config/workflows/registry.yaml``) so the Build Agent is
    given the exact, real list of model deployments Genie can actually use
    - the same list Genie's own Landing page model picker offers - instead
    of inventing fake, ungrounded model-ID strings for any generated
    model-selection UI.
    """

    models = ", ".join(available_models) if available_models else default_model
    return f"{models} (platform default: {default_model})"


def _preview(output_text: str | None) -> str | None:
    """Collapses whitespace and truncates a real agent output for a live trace event."""

    if not output_text:
        return None
    flattened = " ".join(output_text.split())
    if len(flattened) <= _PREVIEW_MAX_LENGTH:
        return flattened
    return f"{flattened[:_PREVIEW_MAX_LENGTH]}..."


def _display_agent_id(step: WorkflowStep, fallback_agent_id: str) -> str:
    """Returns the real specialist agent id a live step event should be attributed to.

    Every ``solution-discovery-workflow`` step's own configured ``agent_id``
    is ``"genie-orchestrator"`` (see ``config/workflows/registry.yaml`` -
    the orchestrator delegates the real work via its one allowed
    ``call_<specialist>`` tool). Live ``step_started``/``step_delta``/
    ``step_completed``/``step_failed`` events shown to users (e.g.
    ``LiveWorkflowPulse``) must attribute to that real specialist - never
    the orchestrator itself - so they read consistently with the delegated
    tool call's own ``step_delta`` events (see
    ``orchestration_tools._stream_and_publish_deltas``), which already tag
    with the specialist's id. Falls back to ``fallback_agent_id`` (the
    step's configured agent) if no delegation is resolvable (e.g. a step
    with no ``allowed_tool_names``).
    """

    for tool_name in step.allowed_tool_names or []:
        target_agent_id = resolve_delegate_agent_id(tool_name)
        if target_agent_id is not None:
            return target_agent_id
    return fallback_agent_id


def _require_complete_requirement_coverage(
    *, step_id: str, variables: dict[str, str], output_text: str
) -> None:
    requirements_text = next(
        (
            variables[name]
            for name in _REQUIREMENT_COVERAGE_VARIABLES
            if variables.get(name)
        ),
        None,
    )
    if step_id not in {"design-architecture", "build-solution"} or not requirements_text:
        return
    if step_id == "build-solution" and _COMPONENT_FAILURE_MARKER in output_text:
        raise FoundryUnavailableError(
            "Workflow step 'build-solution' contains a failed generated component; "
            "partial placeholder artifacts cannot proceed to deployment."
        )
    missing_ids = missing_requirement_ids(requirements_text, output_text)
    if missing_ids:
        raise FoundryUnavailableError(
            f"Workflow step '{step_id}' omitted approved requirement ids: "
            + ", ".join(missing_ids)
        )


class MissingPromptError(RuntimeError):
    """Raised when a step has no resolvable prompt id (fail closed)."""


class MissingMemoryReferenceError(RuntimeError):
    """Raised when a step's required shared memory reference is missing (fail closed)."""


class WorkflowStepExecutor:
    """Resolves and executes a single workflow step via the injected ``AgentGateway``."""

    def __init__(
        self,
        *,
        agent_registry: AgentRegistry,
        prompt_registry: PromptRegistry,
        agent_gateway: AgentGateway,
        governance_service: GovernanceService,
        memory_service: MemoryService | None = None,
        event_bus: WorkflowEventBus | None = None,
        model_catalog_service: ModelCatalogService | None = None,
    ) -> None:
        self._agent_registry = agent_registry
        self._prompt_registry = prompt_registry
        self._agent_gateway = agent_gateway
        self._governance_service = governance_service
        self._memory_service = memory_service
        self._event_bus = event_bus
        self._model_catalog_service = model_catalog_service

    async def execute_step(
        self,
        *,
        step: WorkflowStep,
        session_id: str,
        trace_id: str,
        correlation_id: str,
        step_input: WorkflowStepInput | None = None,
        transcript_text: str = "",
        step_outputs: dict[str, str] | None = None,
        step_variables: dict[str, dict[str, str]] | None = None,
        previous_variables: dict[str, str] | None = None,
        agent_scope_id: str | None = None,
        workflow_run_id: str = "",
    ) -> WorkflowStepResult:
        started_at = datetime.now(UTC)
        agent = get_enabled_agent(self._agent_registry, step.agent_id)

        await self._check_required_memory_references(
            step=step, agent_id=agent.id, session_id=session_id, trace_id=trace_id
        )

        prompt_id = (step_input.prompt_id if step_input else None) or step.prompt_id
        if not prompt_id:
            raise MissingPromptError(
                f"Workflow step '{step.id}' has no prompt_id configured and none was "
                f"supplied for this run."
            )

        variables = await self._resolve_variables(
            step=step,
            transcript_text=transcript_text,
            step_outputs=step_outputs or {},
            step_variables=step_variables or {},
            step_input=step_input,
            previous_variables=previous_variables,
            agent=agent,
            session_id=session_id,
            trace_id=trace_id,
        )
        request = AgentExecutionRequest(
            agent_id=agent.id,
            prompt_id=prompt_id,
            variables=variables,
            correlation_id=correlation_id,
            session_id=session_id,
            agent_scope_id=agent_scope_id,
            allowed_tool_names=step.allowed_tool_names,
        )
        # resolve_prompt_text is used only to fail fast (MissingPromptError's
        # sibling PromptResolutionError/UnknownPromptError) before handing
        # off to the gateway, which performs the same resolution internally.
        resolve_prompt_text(self._prompt_registry, request)

        result = await self._run_agent(
            request=request,
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            step=step,
            agent=agent,
        )

        # Recorded immediately once *this* agent's real call returns - not
        # batched until the whole workflow run/resume call completes. The
        # session's governance event trail (GET /sessions/{id}/governance/
        # events) is therefore a genuinely live, per-agent-call feed: a
        # concurrent poll can observe this event the moment it happens, even
        # while a later step in the same run/resume call is still executing.
        # ``output_preview`` carries a truncated slice of the same real
        # output text returned above - never synthetic content - so live
        # consumers (e.g. the Triage traceability panel) do not have to wait
        # for the full ``WorkflowRunResult`` to be stored to show a concise
        # summary of what the agent actually produced.
        await self._governance_service.record_execution(
            session_id=session_id,
            trace_id=trace_id,
            agent_id=agent.id,
            detail={
                "step_id": step.id,
                "workflow_step": True,
                "output_preview": _preview(result.output_text),
            },
        )

        output_text = result.output_text
        if _display_agent_id(step, agent.id) != agent.id and self._memory_service is not None:
            stored_output = await self._read_step_output(
                source_step_id=step.id,
                step_outputs={},
                agent=agent,
                session_id=session_id,
                trace_id=trace_id,
            )
            if stored_output:
                output_text = stored_output
            elif _DELEGATED_OUTPUT_MARKER in (result.output_text or ""):
                raise FoundryUnavailableError(
                    f"Delegated workflow step '{step.id}' returned the delegation marker "
                    "without storing specialist output in shared memory."
                )

        _require_complete_requirement_coverage(
            step_id=step.id,
            variables=variables,
            output_text=output_text or "",
        )

        return WorkflowStepResult(
            step_id=step.id,
            agent_id=agent.id,
            status="completed",
            output_text=output_text,
            started_at=started_at,
            completed_at=datetime.now(UTC),
            resolved_variables=variables,
        )

    async def _run_agent(
        self,
        *,
        request: AgentExecutionRequest,
        session_id: str,
        workflow_run_id: str,
        step: WorkflowStep,
        agent: AgentDefinition,
    ) -> AgentExecutionResult:
        """Executes ``request``, publishing live step events when an event bus is wired.

        With no ``event_bus`` configured (the default - e.g. most unit
        tests), this is exactly ``await self._agent_gateway.execute(request)``
        with no behavior change at all. With one configured, this instead
        drives the gateway's ``execute_stream``, publishing ``step_started``
        before the call, one ``step_delta`` per incremental chunk the
        gateway yields, and ``step_completed``/``step_failed`` once the
        stream ends - so a concurrent SSE subscriber
        (``GET /sessions/{id}/workflow-events/stream``) sees this step come
        to life in real time, without changing what governance records or
        what this method returns to its caller.
        """

        if self._event_bus is None or not workflow_run_id:
            return await self._agent_gateway.execute(request)

        display_agent_id = _display_agent_id(step, agent.id)
        # A step is "delegated" when its allowed_tool_names resolve to a
        # real specialist (display_agent_id differs from this step's own
        # configured agent.id, always "genie-orchestrator"). Every
        # orchestrator-*-phase-v1 prompt (config/prompts/registry.yaml)
        # instructs the model to, once the delegated call returns, "respond
        # with EXACTLY that tool's returned output text and nothing else" -
        # so genie-orchestrator's own completion below is always a verbatim
        # re-typing of content the delegated tool call already published its
        # own step_delta events for, tagged with this same
        # display_agent_id/step_id (see
        # orchestration_tools._stream_and_publish_deltas). Re-publishing
        # that echoed completion's own deltas here would make a live
        # consumer (e.g. the Workshop page's per-component build view)
        # accumulate the whole already-finished generation a second time,
        # appearing to restart from the beginning right after it just
        # completed. Delegated steps therefore only publish the
        # step_started/step_completed lifecycle events here - step_delta is
        # left entirely to the delegated tool call's own publishing.
        is_delegated = display_agent_id != agent.id

        await self._event_bus.publish(
            WorkflowStreamEvent(
                event_type="step_started",
                session_id=session_id,
                workflow_run_id=workflow_run_id,
                step_id=step.id,
                agent_id=display_agent_id,
            )
        )
        try:
            result: AgentExecutionResult | None = None
            async for chunk in self._agent_gateway.execute_stream(request):
                if chunk.delta and not is_delegated:
                    await self._event_bus.publish(
                        WorkflowStreamEvent(
                            event_type="step_delta",
                            session_id=session_id,
                            workflow_run_id=workflow_run_id,
                            step_id=step.id,
                            agent_id=display_agent_id,
                            delta=chunk.delta,
                        )
                    )
                if chunk.result is not None:
                    result = chunk.result
        except Exception as exc:
            await self._event_bus.publish(
                WorkflowStreamEvent(
                    event_type="step_failed",
                    session_id=session_id,
                    workflow_run_id=workflow_run_id,
                    step_id=step.id,
                    agent_id=display_agent_id,
                    error=str(exc),
                )
            )
            raise

        if result is None:
            reason = f"Agent gateway stream for step '{step.id}' ended without a final result."
            await self._event_bus.publish(
                WorkflowStreamEvent(
                    event_type="step_failed",
                    session_id=session_id,
                    workflow_run_id=workflow_run_id,
                    step_id=step.id,
                    agent_id=display_agent_id,
                    error=reason,
                )
            )
            raise RuntimeError(reason)

        # For a delegated step, genie-orchestrator's own raw output_text is
        # never useful to preview here: it is either a verbatim echo of
        # content the delegated tool call's own step_delta events already
        # showed in full, or (when the specialist's real output was too
        # large to inline and was stored to shared memory instead) literally
        # the internal _DELEGATED_OUTPUT_MARKER sentinel string - showing
        # either as this step's "completed" preview is either redundant or
        # a confusing internal-implementation-detail leak into the user
        # facing activity banner (e.g. "... completed: DELEGATED_OUTPUT_STORED").
        completed_preview = None if is_delegated else _preview(result.output_text)
        await self._event_bus.publish(
            WorkflowStreamEvent(
                event_type="step_completed",
                session_id=session_id,
                workflow_run_id=workflow_run_id,
                step_id=step.id,
                agent_id=display_agent_id,
                output_preview=completed_preview,
            )
        )
        return result


    async def _resolve_variables(
        self,
        *,
        step: WorkflowStep,
        transcript_text: str,
        step_outputs: dict[str, str],
        step_variables: dict[str, dict[str, str]] | None = None,
        step_input: WorkflowStepInput | None,
        previous_variables: dict[str, str] | None = None,
        agent: AgentDefinition,
        session_id: str,
        trace_id: str,
    ) -> dict[str, str]:
        """Merges ``step.variable_sources``-derived values with explicit overrides.

        Explicit ``step_input.variables`` always win over anything derived
        from ``variable_sources`` - callers can still fully control a run.
        Variables with no source and no explicit override are simply
        omitted, so an under-specified step still fails closed via
        ``resolve_prompt_text``'s missing-variable check rather than
        silently sending a blank value.

        ``previous_variables`` (a prior execution's ``resolved_variables``,
        when this step is being re-executed on a resumed run - e.g. a
        customer chat message) seeds the base layer before
        ``variable_sources``/``step_input`` are applied. This preserves any
        variable that can only ever be supplied via an explicit override
        (such as governance-review's ``policies``) across re-runs that only
        intend to change an unrelated variable like ``user_message`` -
        without it, re-running an already-completed step would otherwise
        always fail closed with a missing-variable error.

        ``user_message`` always defaults to an empty string so that any
        prompt template which declares it (customer/user chat interactions
        - see ``app.api.cx``'s ``/chat`` route) can rely on it always being
        present, without requiring every non-chat workflow run to supply it
        explicitly. Prompts that do not reference ``{user_message}`` in
        their text are entirely unaffected: an unused ``str.format`` kwarg
        never changes the resolved output.

        For a ``step:<id>`` source, the *real* upstream specialist output is
        read back from Shared Collaboration Memory first (written by that
        specialist's own delegation tool call - see
        ``app.agents.tools.orchestration_tools``) rather than trusting only
        the in-process ``step_outputs`` dict, which holds genie-orchestrator's
        own relayed final message for that step and could in principle
        diverge from the delegated specialist's actual output (e.g. if the
        model paraphrases instead of relaying verbatim). ``step_outputs`` is
        kept as a fallback for setups with no ``MemoryService`` (e.g. some
        unit tests) or steps whose output was never written to shared
        memory.
        """

        resolved: dict[str, str] = {"user_message": "", **(previous_variables or {})}
        for variable_name, source in step.variable_sources.items():
            if source == "transcript":
                resolved[variable_name] = transcript_text
            elif source == _MODEL_CATALOG_SOURCE:
                if self._model_catalog_service is not None:
                    catalog = await self._model_catalog_service.get_catalog()
                    resolved[variable_name] = _format_model_catalog(
                        catalog.available_models, catalog.default_model
                    )
            elif source.startswith("step-variable:"):
                _, source_step_id, source_variable_name = source.split(":", maxsplit=2)
                source_value = (step_variables or {}).get(source_step_id, {}).get(
                    source_variable_name
                )
                if source_value is not None:
                    resolved[variable_name] = source_value
            elif source.startswith("step:"):
                source_step_id = source.removeprefix("step:")
                value = await self._read_step_output(
                    source_step_id=source_step_id,
                    step_outputs=step_outputs,
                    agent=agent,
                    session_id=session_id,
                    trace_id=trace_id,
                )
                if value is not None:
                    resolved[variable_name] = value

        if step_input:
            resolved.update(step_input.variables)
        return resolved

    async def _read_step_output(
        self,
        *,
        source_step_id: str,
        step_outputs: dict[str, str],
        agent: AgentDefinition,
        session_id: str,
        trace_id: str,
    ) -> str | None:
        if self._memory_service is not None and "shared" in agent.memory_access:
            records = await self._memory_service.shared.read(
                requesting_agent=agent, session_id=session_id, trace_id=trace_id, key=source_step_id
            )
            if records:
                output_text = records[0].content.get("output_text")
                if isinstance(output_text, str) and output_text:
                    return output_text
        return step_outputs.get(source_step_id)

    async def _check_required_memory_references(
        self, *, step: WorkflowStep, agent_id: str, session_id: str, trace_id: str
    ) -> None:
        if not step.required_memory_references:
            return
        if self._memory_service is None:
            raise MissingMemoryReferenceError(
                f"Workflow step '{step.id}' requires memory references "
                f"{step.required_memory_references} but no MemoryService is configured."
            )

        agent = get_enabled_agent(self._agent_registry, step.agent_id)
        for key in step.required_memory_references:
            records = await self._memory_service.shared.read(
                requesting_agent=agent, session_id=session_id, trace_id=trace_id, key=key
            )
            if not records:
                raise MissingMemoryReferenceError(
                    f"Workflow step '{step.id}' requires shared memory reference "
                    f"'{key}' which does not exist for session '{session_id}'."
                )

        await self._governance_service.record_memory_read(
            session_id=session_id,
            trace_id=trace_id,
            agent_id=agent_id,
            detail={"step_id": step.id, "keys": step.required_memory_references},
        )
