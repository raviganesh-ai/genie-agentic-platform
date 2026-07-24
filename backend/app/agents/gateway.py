"""Agent execution gateway selection.

See "Production Agent Rules" in ``.github/copilot-instructions.md``:
production must execute exclusively through Azure AI Foundry via
``AzureAgentGateway``, never fall back to local or mock execution, and must
fail closed if Foundry is unavailable or misconfigured.

This module (and ``azure_agent_gateway.py``) contain no agent reasoning of
any kind. Every Genie business agent is an independently deployed Azure AI
Foundry agent resource (see ``AgentDefinition.foundry_agent_id``); this
module only resolves *which* registered agent and *which* registered
prompt template a request names, then hands execution off to a gateway.
"""
from __future__ import annotations

from typing import Protocol

from app.agents.models import AgentDefinition, AgentExecutionRequest, AgentExecutionResult
from app.agents.registry import AgentRegistry
from app.config.settings import Settings
from app.prompts.registry import PromptRegistry


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


class LocalAgentGateway:
    """Deterministic, non-network agent execution for local development only.

    Never used in production: ``ProductionSafetyValidator`` fails closed if
    ``allow_local_agents`` is True while ``provider_mode`` is production, and
    ``create_agent_gateway`` below never returns this gateway in production.
    It performs no reasoning of any kind - it only proves that agent/prompt
    resolution succeeded, so developers can exercise the request/response
    contract without needing real Azure AI Foundry credentials.
    """

    def __init__(self, agent_registry: AgentRegistry, prompt_registry: PromptRegistry) -> None:
        self._agent_registry = agent_registry
        self._prompt_registry = prompt_registry

    async def execute(self, request: AgentExecutionRequest) -> AgentExecutionResult:
        agent = get_enabled_agent(self._agent_registry, request.agent_id)
        resolved_text = resolve_prompt_text(self._prompt_registry, request)
        output_text = (
            f"[local-agent-gateway] agent='{agent.id}' "
            f"resolved_prompt_length={len(resolved_text)}"
        )
        return AgentExecutionResult(
            agent_id=agent.id,
            model_deployment_ref=agent.model_deployment_ref or "",
            output_text=output_text,
            correlation_id=request.correlation_id,
        )


def create_agent_gateway(
    *,
    settings: Settings,
    agent_registry: AgentRegistry,
    prompt_registry: PromptRegistry,
    governance_recorder: GovernanceTraceRecorder | None = None,
) -> AgentGateway:
    """Select the single execution gateway for the current provider mode.

    Production: always ``AzureAgentGateway``. Never falls back to
    ``LocalAgentGateway``; raises ``AgentGatewayError`` (fail closed) if
    Foundry is not configured.

    Local/dev: ``LocalAgentGateway`` when ``allow_local_agents`` is True,
    otherwise ``AzureAgentGateway`` if Foundry is configured, otherwise
    raises.
    """

    # Local import: keeps the (lazily-imported) azure-ai-projects SDK import
    # chain out of the local/dev hot path and avoids a circular import, since
    # azure_agent_gateway.py imports helpers from this module.
    from app.agents.azure_agent_gateway import AzureAgentGateway
    from app.agents.foundry.agent_provider import FoundryAgentProvider
    from app.agents.foundry.project_service import FoundryProjectService

    recorder = governance_recorder or NullGovernanceTraceRecorder()

    def _build_azure_gateway() -> AgentGateway:
        if not settings.azure_foundry_endpoint or not settings.azure_foundry_project_name:
            raise AgentGatewayError(
                "azure_foundry_endpoint and azure_foundry_project_name must be "
                "configured to use AzureAgentGateway."
            )
        project_service = FoundryProjectService(
            endpoint=settings.azure_foundry_endpoint,
            project_name=settings.azure_foundry_project_name,
        )
        return AzureAgentGateway(
            agent_registry=agent_registry,
            prompt_registry=prompt_registry,
            foundry_client=FoundryAgentProvider(project_service),
            governance_recorder=recorder,
        )

    if settings.provider_mode == "production":
        # Fail closed: production must never fall back to local execution,
        # so this always resolves to AzureAgentGateway or raises.
        return _build_azure_gateway()

    if settings.allow_local_agents:
        return LocalAgentGateway(agent_registry, prompt_registry)

    if settings.azure_foundry_endpoint and settings.azure_foundry_project_name:
        return _build_azure_gateway()

    raise AgentGatewayError(
        "No usable agent execution gateway: allow_local_agents is False and "
        "Azure AI Foundry is not configured."
    )
