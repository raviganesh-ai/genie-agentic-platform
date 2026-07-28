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

from app.agents.gateway import AgentGateway, get_enabled_agent, resolve_prompt_text
from app.agents.models import AgentExecutionRequest
from app.agents.registry import AgentRegistry
from app.governance.governance_service import GovernanceService
from app.memory.memory_service import MemoryService
from app.models.workflow_models import WorkflowStepInput, WorkflowStepResult
from app.prompts.registry import PromptRegistry
from app.workflows.models import WorkflowStep

__all__ = ["MissingMemoryReferenceError", "MissingPromptError", "WorkflowStepExecutor"]

_PREVIEW_MAX_LENGTH = 240


def _preview(output_text: str | None) -> str | None:
    """Collapses whitespace and truncates a real agent output for a live trace event."""

    if not output_text:
        return None
    flattened = " ".join(output_text.split())
    if len(flattened) <= _PREVIEW_MAX_LENGTH:
        return flattened
    return f"{flattened[:_PREVIEW_MAX_LENGTH]}..."


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
    ) -> None:
        self._agent_registry = agent_registry
        self._prompt_registry = prompt_registry
        self._agent_gateway = agent_gateway
        self._governance_service = governance_service
        self._memory_service = memory_service

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
        previous_variables: dict[str, str] | None = None,
        agent_scope_id: str | None = None,
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

        variables = self._resolve_variables(
            step=step,
            transcript_text=transcript_text,
            step_outputs=step_outputs or {},
            step_input=step_input,
            previous_variables=previous_variables,
        )
        request = AgentExecutionRequest(
            agent_id=agent.id,
            prompt_id=prompt_id,
            variables=variables,
            correlation_id=correlation_id,
            session_id=session_id,
            agent_scope_id=agent_scope_id,
        )
        # resolve_prompt_text is used only to fail fast (MissingPromptError's
        # sibling PromptResolutionError/UnknownPromptError) before handing
        # off to the gateway, which performs the same resolution internally.
        resolve_prompt_text(self._prompt_registry, request)

        result = await self._agent_gateway.execute(request)

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

        return WorkflowStepResult(
            step_id=step.id,
            agent_id=agent.id,
            status="completed",
            output_text=result.output_text,
            started_at=started_at,
            completed_at=datetime.now(UTC),
            resolved_variables=variables,
        )

    def _resolve_variables(
        self,
        *,
        step: WorkflowStep,
        transcript_text: str,
        step_outputs: dict[str, str],
        step_input: WorkflowStepInput | None,
        previous_variables: dict[str, str] | None = None,
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
        """

        resolved: dict[str, str] = {"user_message": "", **(previous_variables or {})}
        for variable_name, source in step.variable_sources.items():
            if source == "transcript":
                resolved[variable_name] = transcript_text
            elif source.startswith("step:"):
                source_step_id = source.removeprefix("step:")
                if source_step_id in step_outputs:
                    resolved[variable_name] = step_outputs[source_step_id]

        if step_input:
            resolved.update(step_input.variables)
        return resolved

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
