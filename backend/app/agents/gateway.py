"""Azure AI Foundry-only agent execution gateway.

This module (and ``azure_agent_gateway.py``) contain no agent reasoning of
any kind. Every Genie business agent is an independently deployed Azure AI
Foundry agent resource (see ``AgentDefinition.foundry_agent_id``); this
module only resolves *which* registered agent and *which* registered
prompt template a request names, then hands execution off to a gateway.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Protocol

from app.agents.models import (
    AgentDefinition,
    AgentExecutionRequest,
    AgentExecutionResult,
    AgentExecutionStreamChunk,
)
from app.agents.registry import AgentRegistry
from app.config.settings import Settings
from app.prompts.registry import PromptRegistry

if TYPE_CHECKING:
    from app.agents.tool_execution import AgentToolRegistry


class AgentGatewayError(RuntimeError):
    """Base error for agent execution gateway failures."""


class UnknownAgentError(AgentGatewayError):
    """Raised when an execution request references an unknown or disabled agent."""


class UnknownPromptError(AgentGatewayError):
    """Raised when an execution request references an unknown prompt template."""


class PromptResolutionError(AgentGatewayError):
    """Raised when the supplied variables cannot resolve a prompt template."""


class GovernanceTraceRecorder(Protocol):
    """Seam for recording governance trace events during agent execution.

    A full governance provider (``Agent365GovernanceProvider`` /
    ``LocalGovernanceTraceProvider``) is implemented in Phase 5. Until then,
    ``NullGovernanceTraceRecorder`` is used so gateway call sites will not
    need to change once Phase 5 lands.
    """

    def record_execution(
        self, *, request: AgentExecutionRequest, result: AgentExecutionResult
    ) -> None: ...

    def record_unavailable(self, *, request: AgentExecutionRequest, reason: str) -> None: ...


class SessionAgentResolver(Protocol):
    """Seam for resolving a customer session's own dedicated Foundry agent.

    Implemented by ``CustomerAgentProvisioningService``
    (``app.services.customer_agent_provisioning_service``). When a session
    has had dedicated per-customer Foundry agents provisioned,
    ``AzureAgentGateway`` executes against that dedicated agent id instead
    of the shared, statically configured ``AgentDefinition.foundry_agent_id``
    - so customer chat/reanalysis interactions never reach the same Foundry
    agent resource another customer's session uses.

    ``scope_id``, when supplied (e.g. a requirement group id), narrows the
    lookup to a fleet dedicated to that scope rather than the whole
    session - see "dedicated fleet of agents ... per requirement" in
    ``docs/GENIE_BUILD_SPEC.md``. Implementations fall back to ``session_id``
    when ``scope_id`` is None.
    """

    def resolve(self, *, session_id: str, agent_id: str, scope_id: str | None = None) -> str | None: ...


class NullGovernanceTraceRecorder:
    """No-op ``GovernanceTraceRecorder`` used until Phase 5 governance services exist."""

    def record_execution(
        self, *, request: AgentExecutionRequest, result: AgentExecutionResult
    ) -> None:
        return None

    def record_unavailable(self, *, request: AgentExecutionRequest, reason: str) -> None:
        return None


class AgentGateway(Protocol):
    """Executes an agent against a resolved prompt template."""

    async def execute(self, request: AgentExecutionRequest) -> AgentExecutionResult: ...

    def execute_stream(
        self, request: AgentExecutionRequest
    ) -> AsyncIterator[AgentExecutionStreamChunk]:
        """Streams incremental output text, ending with a chunk carrying the final result.

        Every implementation performs the exact same governance recording
        and error handling as ``execute`` - streaming only changes *when*
        output text becomes visible to the caller, never what gets recorded
        or how failures are handled.
        """
        ...


def get_enabled_agent(agent_registry: AgentRegistry, agent_id: str) -> AgentDefinition:
    try:
        agent = agent_registry.get(agent_id)
    except KeyError as exc:
        raise UnknownAgentError(f"Unknown agent id '{agent_id}'.") from exc
    if not agent.enabled:
        raise UnknownAgentError(f"Agent '{agent_id}' is disabled.")
    return agent


def resolve_prompt_text(prompt_registry: PromptRegistry, request: AgentExecutionRequest) -> str:
    try:
        prompt = prompt_registry.get(request.prompt_id)
    except KeyError as exc:
        raise UnknownPromptError(f"Unknown prompt id '{request.prompt_id}'.") from exc

    missing = set(prompt.variables) - set(request.variables)
    if missing:
        raise PromptResolutionError(
            f"Prompt '{prompt.id}' is missing required variable(s): {sorted(missing)}."
        )
    try:
        return prompt.template.format(**request.variables)
    except (KeyError, IndexError) as exc:
        raise PromptResolutionError(
            f"Prompt '{prompt.id}' could not be resolved with the supplied variables: {exc}"
        ) from exc


def create_agent_gateway(
    *,
    settings: Settings,
    agent_registry: AgentRegistry,
    prompt_registry: PromptRegistry,
    governance_recorder: GovernanceTraceRecorder | None = None,
    session_agent_resolver: SessionAgentResolver | None = None,
    tool_registry: AgentToolRegistry | None = None,
) -> AgentGateway:
    """Create the single Azure AI Foundry execution gateway.

    ``session_agent_resolver``, when supplied, lets ``AzureAgentGateway``
    route a given session's executions to that session's own dedicated
    Foundry agents (see ``CustomerAgentProvisioningService``) instead of the
    shared catalog pool.

    ``tool_registry``, when supplied, lets ``AzureAgentGateway``'s Foundry
    runs resolve and execute function-tool calls. A run that reaches
    ``requires_action`` with no registry configured fails closed.
    """

    # Local import avoids a circular import because azure_agent_gateway.py
    # imports prompt/agent resolution helpers from this module.
    from app.agents.azure_agent_gateway import AzureAgentGateway
    from app.agents.foundry.agent_provider import FoundryAgentProvider
    from app.agents.foundry.project_service import FoundryProjectService

    recorder = governance_recorder or NullGovernanceTraceRecorder()

    if not settings.azure_foundry_endpoint or not settings.azure_foundry_project_name:
        raise AgentGatewayError(
            "azure_foundry_endpoint and azure_foundry_project_name are required; "
            "Genie does not provide a local or mock agent execution path."
        )
    project_service = FoundryProjectService(
        endpoint=settings.azure_foundry_endpoint,
        project_name=settings.azure_foundry_project_name,
    )
    return AzureAgentGateway(
        agent_registry=agent_registry,
        prompt_registry=prompt_registry,
        foundry_client=FoundryAgentProvider(
            project_service,
            tool_registry=tool_registry,
            run_timeout_seconds=settings.foundry_agent_run_timeout_seconds,
        ),
        governance_recorder=recorder,
        session_agent_resolver=session_agent_resolver,
    )
