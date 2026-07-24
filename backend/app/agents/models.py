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
            "The id of the independently deployed Azure AI Foundry agent "
            "resource this Genie agent maps to. This agent's reasoning, "
            "instructions, and tools are owned and versioned in Azure AI "
            "Foundry, never in Genie source code. Required for any agent "
            "executed via AzureAgentGateway in production; omitted only for "
            "agents that are exclusively exercised through LocalAgentGateway "
            "during local development."
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

    @field_validator(
        "model_deployment_ref", "foundry_agent_id", "owner", "governance_policy_id", "prompt_template_ref"
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


class AgentExecutionResult(BaseModel):
    """The output produced by executing one ``AgentExecutionRequest``."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1)
    model_deployment_ref: str = ""
    output_text: str
    correlation_id: str = Field(min_length=1)
