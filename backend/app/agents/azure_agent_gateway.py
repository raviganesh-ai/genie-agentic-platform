"""AzureAgentGateway: the only production agent execution path.

See "Production Agent Rules" in ``.github/copilot-instructions.md``. This
gateway contains no agent reasoning, prompts, workflows, or sample outputs
of its own - every Genie business agent is an independently deployed Azure
AI Foundry agent resource (``AgentDefinition.foundry_agent_id``); this
gateway only resolves the requested prompt template and hands the resolved
text to that agent's Foundry resource via a ``FoundryAgentClient``.

It never falls back to local execution: if the Foundry client raises
``FoundryUnavailableError``, this gateway records a governance trace event
and re-raises rather than substituting a local or mock response.
"""
from __future__ import annotations

from app.agents.foundry.agent_provider import FoundryAgentClient
from app.agents.foundry.errors import FoundryUnavailableError
from app.agents.gateway import (
    GovernanceTraceRecorder,
    NullGovernanceTraceRecorder,
    SessionAgentResolver,
    get_enabled_agent,
    resolve_prompt_text,
)
from app.agents.models import AgentExecutionRequest, AgentExecutionResult
from app.agents.registry import AgentRegistry
from app.agents.tool_execution import ToolCallContext
from app.prompts.registry import PromptRegistry

__all__ = ["AzureAgentGateway"]


class AzureAgentGateway:
    """Executes Genie agents exclusively through their Azure AI Foundry resource."""

    def __init__(
        self,
        *,
        agent_registry: AgentRegistry,
        prompt_registry: PromptRegistry,
        foundry_client: FoundryAgentClient,
        governance_recorder: GovernanceTraceRecorder | None = None,
        session_agent_resolver: SessionAgentResolver | None = None,
    ) -> None:
        self._agent_registry = agent_registry
        self._prompt_registry = prompt_registry
        self._foundry_client = foundry_client
        self._governance_recorder = governance_recorder or NullGovernanceTraceRecorder()
        self._session_agent_resolver = session_agent_resolver

    async def execute(self, request: AgentExecutionRequest) -> AgentExecutionResult:
        agent = get_enabled_agent(self._agent_registry, request.agent_id)

        foundry_agent_id = agent.foundry_agent_id
        if request.session_id and self._session_agent_resolver is not None:
            dedicated_agent_id = self._session_agent_resolver.resolve(
                session_id=request.session_id,
                agent_id=agent.id,
                scope_id=request.agent_scope_id,
            )
            if dedicated_agent_id:
                foundry_agent_id = dedicated_agent_id

        if not foundry_agent_id:
            reason = (
                f"Agent '{agent.id}' has no foundry_agent_id configured; it is "
                f"not deployed as an Azure AI Foundry agent resource and cannot "
                f"be executed by AzureAgentGateway."
            )
            self._governance_recorder.record_unavailable(request=request, reason=reason)
            raise FoundryUnavailableError(reason)

        resolved_text = resolve_prompt_text(self._prompt_registry, request)

        tool_context = ToolCallContext(
            agent=agent,
            session_id=request.session_id,
            trace_id=request.correlation_id,
            allowed_tool_names=request.allowed_tool_names,
            variables=request.variables,
        )
        try:
            run_result = await self._foundry_client.run(
                foundry_agent_id=foundry_agent_id,
                input_text=resolved_text,
                tool_context=tool_context,
            )
        except FoundryUnavailableError as exc:
            self._governance_recorder.record_unavailable(request=request, reason=str(exc))
            raise

        result = AgentExecutionResult(
            agent_id=agent.id,
            model_deployment_ref=agent.model_deployment_ref or "",
            output_text=run_result.output_text,
            correlation_id=request.correlation_id,
        )
        self._governance_recorder.record_execution(request=request, result=result)
        return result
