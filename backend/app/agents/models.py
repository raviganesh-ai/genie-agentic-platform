"""Strongly typed agent registry domain models.

Instances are populated exclusively from YAML files under the configured
agents directory (see ``Settings.agents_path``); agent definitions must
never be hardcoded in source, per the Configuration Rules in
``.github/copilot-instructions.md``.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MemoryTier = Literal["personal", "shared", "enterprise"]

ToolParameterType = Literal["string", "number", "integer", "boolean", "array", "object"]


class AgentToolParameter(BaseModel):
    """One JSON-schema parameter of an ``AgentToolDefinition``.

    Rendered into the ``parameters`` JSON schema passed to Azure AI
    Foundry's ``FunctionTool`` when an agent resource is provisioned/
    synchronized - never hardcoded per-agent in source, per the
    Configuration Rules in ``.github/copilot-instructions.md``.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    type: ToolParameterType
    description: str = Field(min_length=1)
    required: bool = True


class AgentToolDefinition(BaseModel):
    """An externally configured function tool an agent may call at runtime.

    Distinct from ``allowed_tools`` (a flat list of provider-managed tool
    names such as ``azure_ai_search``): each entry here names a genuine
    Python function-calling tool, dispatched via ``AgentToolRegistry``
    (``app.agents.tool_execution``) when the agent's Foundry run reaches a
    ``requires_action`` status.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    parameters: list[AgentToolParameter] = Field(default_factory=list)


class AgentDefinition(BaseModel):
    """A single agent's externally configured identity and capabilities."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    role: str = Field(min_length=1)
    description: str = Field(min_length=1)
    capabilities: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    memory_access: list[MemoryTier] = Field(default_factory=lambda: ["personal"])
    model_deployment_ref: str | None = Field(
        default=None,
        description=(
            "Optional per-agent LLM override. When unset, the agent falls "
            "back to Settings.default_llm (configurable via GENIE_DEFAULT_LLM "
            "or the Model Settings UI). Informational/telemetry only - the "
            "model actually used at runtime is whatever the referenced "
            "foundry_agent_id resource is configured with in Azure AI Foundry."
        ),
    )
    foundry_agent_id: str | None = Field(
        default=None,
        description=(
            "The Azure AI Foundry Prompt Agent 'agent_name' this Genie agent "
            "maps to (a human-readable resource name, e.g. "
            "'requirements-analyst' - used with the versioned "
            "azure-ai-projects agents.get/create_version/delete API, not a "
            "raw 'asst_...' id). This agent's reasoning, instructions, and "
            "tools are owned and versioned in Azure AI Foundry, never in "
            "Genie source code. Required for any agent executed via "
            "AzureAgentGateway in production; omitted only for agents that "
            "are exclusively exercised through LocalAgentGateway during "
            "local development."
        ),
    )
    foundry_agent_version: str | None = Field(
        default=None,
        description=(
            "Optional pinned Foundry agent version (see foundry_agent_id). "
            "When unset, execution resolves the resource's latest published "
            "version at run time via AgentApiClient.get_latest_version - "
            "never guessed or hardcoded."
        ),
    )

    enabled: bool = True
    version: str = Field(
        default="1.0.0",
        min_length=1,
        description=(
            "The agent definition's own externally configured version, "
            "independent of its foundry_agent_id or model_deployment_ref. "
            "Recorded as agentVersion in every memory write's lineage for "
            "governance traceability and decision lineage (see Memory "
            "Architecture in .github/copilot-instructions.md)."
        ),
    )
    owner: str | None = Field(
        default=None,
        description=(
            "The team or role that owns this agent's configuration and "
            "Foundry resource (e.g. 'requirements-team'). Additive Phase "
            "10A metadata used by Foundry agent inventory/provisioning; "
            "never a person's name or other customer/PII data."
        ),
    )
    governance_policy_id: str | None = Field(
        default=None,
        description=(
            "Identifier of the governance policy this agent is subject to. "
            "Additive Phase 10A metadata verified by "
            "FoundryAgentRegistryValidator/FoundryAgentSynchronizationService; "
            "required for any agent executed via AzureAgentGateway in "
            "production."
        ),
    )
    prompt_template_ref: str | None = Field(
        default=None,
        description=(
            "Id of this agent's primary registered prompt template (see "
            "config/prompts/*.yaml, resolved via PromptRegistry). Additive "
            "Phase 10A metadata distinct from any per-workflow-step "
            "prompt_id override."
        ),
    )
    connected_agent_ids: list[str] | None = Field(
        default=None,
        description=(
            "Ids of other registered agents this agent calls dynamically "
            "via Azure AI Foundry's Connected Agents tool feature (as "
            "opposed to a fixed declarative workflow step sequence). Only "
            "meaningful for orchestrator-style agents; every id must "
            "resolve to another agent in this same registry (see "
            "AgentRegistry.load's cross-reference validation)."
        ),
    )
    tool_definitions: list[AgentToolDefinition] = Field(
        default_factory=list,
        description=(
            "Externally configured function-calling tools this agent may "
            "invoke at runtime (see AgentToolDefinition). Each name must "
            "have a matching implementation registered in the runtime "
            "AgentToolRegistry (app.agents.tool_execution) for the agent's "
            "id, or a run reaching 'requires_action' for this tool fails "
            "closed."
        ),
    )

    @field_validator(
        "model_deployment_ref",
        "foundry_agent_id",
        "foundry_agent_version",
        "owner",
        "governance_policy_id",
        "prompt_template_ref",
    )
    @classmethod
    def _non_blank_if_set(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("value must not be blank when provided.")
        return value


class AgentExecutionRequest(BaseModel):
    """A single request to execute one Genie agent against one resolved prompt.

    Carries no agent reasoning or business logic itself - it only names
    *which* registered agent and *which* registered prompt template to
    resolve and send to that agent's Azure AI Foundry resource.
    """

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1)
    prompt_id: str = Field(min_length=1)
    variables: dict[str, str] = Field(default_factory=dict)
    correlation_id: str = Field(min_length=1)
    session_id: str | None = None
    agent_scope_id: str | None = Field(
        default=None,
        description=(
            "Optional dedicated-agent-fleet scope narrower than session_id, "
            "e.g. a requirement group id - see SessionAgentResolver.resolve's "
            "scope_id parameter."
        ),
    )
    allowed_tool_names: list[str] | None = Field(
        default=None,
        description=(
            "Optional request-scoped restriction of which of the agent's own "
            "AgentDefinition.tool_definitions may be exposed to this specific "
            "run. None means every configured tool is exposed (today's "
            "behavior for every non-orchestrator agent). Used by phase-scoped "
            "genie-orchestrator workflow steps so a given phase call only "
            "exposes the one or two delegation tools relevant to that phase, "
            "rather than every connected agent at once - never used to grant "
            "a tool the agent does not already have configured."
        ),
    )


class AgentExecutionResult(BaseModel):
    """The output produced by executing one ``AgentExecutionRequest``."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1)
    model_deployment_ref: str = ""
    output_text: str
    correlation_id: str = Field(min_length=1)


class AgentExecutionStreamChunk(BaseModel):
    """One item yielded by ``AgentGateway.execute_stream``.

    Every chunk except the last carries a non-empty ``delta`` (an
    incremental slice of the agent's response text) and ``result=None``.
    The last chunk carries ``delta=None`` and a populated ``result`` with
    the same ``AgentExecutionResult`` shape ``execute()`` returns, so
    callers that only want the finished result can consume the stream and
    keep whichever chunk has ``result`` set.
    """

    model_config = ConfigDict(extra="forbid")

    delta: str | None = None
    result: AgentExecutionResult | None = None
