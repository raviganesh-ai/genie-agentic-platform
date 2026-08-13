"""Strongly typed, externally sourced application settings.

No configuration value in this module may be hardcoded to a real endpoint,
credential, tenant, subscription, or customer value. Settings are sourced
exclusively from environment variables (optionally via a local ``.env``
file), per the Configuration Rules in ``.github/copilot-instructions.md``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

GovernanceProviderName = Literal["local", "agent365"]
Environment = Literal["development", "test", "production"]
MemoryStoreBackend = Literal["in_memory", "cosmos_db"]

# Default LLM used by every agent unless it (or the caller) supplies an
# explicit override. Not a secret or endpoint - a plain application default,
# externally overridable via GENIE_DEFAULT_LLM. "gpt-5-1" (hyphenated) is
# used because it is sold directly by Azure (no Azure Marketplace
# subscription/quota required) AND because it is the EXACT Cognitive
# Services deployment name provisioned on the real Foundry account - Foundry
# agent run-time model resolution requires an exact deployment-name match
# (unlike agent create/update, which silently accepts other string forms
# such as the dotted "gpt-5.1" and only fails when a run is actually
# attempted). Anthropic Claude models require a Marketplace subscription
# and, as of this writing, this subscription has a default quota of 0 for
# every Claude SKU.
DEFAULT_LLM = "gpt-5-1"


class Settings(BaseSettings):
    """Strongly typed application settings.

    All values are externally configured via environment variables prefixed
    with ``GENIE_`` (e.g. ``GENIE_ALLOW_LOCAL_AGENTS``). See ``.env.example``
    for the full list of supported variables.
    """

    model_config = SettingsConfigDict(
        env_prefix="GENIE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Core service identity ------------------------------------------------
    service_name: str = "genie-backend"
    environment: Environment = "development"
    log_level: str = "INFO"

    # --- Execution mode -----------------------------------------------------------
    # Genie is a personal dev/demo deployment - there is no separate
    # production tier, so these flags simply choose real Azure services
    # (Foundry, etc.) when configured, falling back to local/mock stand-ins
    # otherwise. See app.agents.gateway.create_agent_gateway and its sibling
    # factories (governance, customer/mission agent provisioning, deploy
    # pipeline, speech-to-text) for exactly how each one is selected.
    governance_provider: GovernanceProviderName = "local"
    allow_mock_agents: bool = True
    allow_local_agents: bool = True
    use_synthetic_data: bool = True
    # Controls whether create_token_validator() may fall back to
    # LocalDevTokenValidator (unverified-signature JWT decode). Independent
    # of allow_local_agents (which selects LocalAgentGateway vs
    # AzureAgentGateway for agent *execution*) so the two concerns can be
    # configured independently.
    allow_local_token_validation: bool = True

    # --- Azure AI Foundry ---------------------------------------------------------
    azure_foundry_endpoint: str | None = None
    azure_foundry_project_name: str | None = None

    # Azure subscription every real Deploy & Launch pipeline Azure mgmt SDK
    # call (ACR, Container Apps, Storage) targets. Never hardcoded to a real
    # subscription id (Configuration Rules).
    azure_subscription_id: str | None = None

    # --- Azure AI Speech (call transcript/recording transcription) --------------
    # Full resource endpoint host, e.g. "https://<resource>.cognitiveservices.azure.com"
    # - either a dedicated Speech resource or a unified AIServices account that
    # also hosts Foundry. Never a hardcoded real endpoint (Configuration Rules).
    azure_speech_endpoint: str | None = None

    # --- Memory store backend ----------------------------------------------------
    # "in_memory" is the only backend implemented in Phase 4 and is safe only
    # for local development and tests; production must configure a durable,
    # externally reachable backend (Cosmos DB / Azure SQL per the
    # Architecture Principles in .github/copilot-instructions.md).
    memory_store_backend: MemoryStoreBackend = "in_memory"
    memory_store_endpoint: str | None = None

    # --- Governance / lineage storage backend ------------------------------------
    # "in_memory" is the only backend implemented in Phase 5 and is safe only
    # for local development and tests; production must configure a durable,
    # externally reachable backend for governance events, recommendation
    # lineage, decision graphs, and approval records.
    lineage_store_backend: MemoryStoreBackend = "in_memory"
    lineage_store_endpoint: str | None = None

    # --- LLM selection ----------------------------------------------------------
    # Default model every agent uses unless it declares its own override in
    # config/agents/*.yaml. Exposed to the UI/API so operators can switch it
    # per-deployment without a code change.
    default_llm: str = DEFAULT_LLM

    # --- Debugging workflow (Phase 6) --------------------------------------------
    # Id of the workflow (config/workflows/*.yaml) the orchestration runtime
    # invokes on FailureDetected. Debugging agents are Azure-hosted agents
    # executed through the same AzureAgentGateway as every other agent -
    # never local, never mocked, per the Production Agent Rules.
    debugging_workflow_id: str = "debugging-workflow"

    # --- Security -------------------------------------------------------------------
    key_vault_uri: str | None = None

    # Id of the workflow step (config/workflows/*.yaml) whose output_text
    # carries the Requirements Analyst agent's structured agentic-workflow
    # qualification verdict (see app.services.requirements_service). Mirrors
    # debugging_workflow_id's pattern of naming a config entity from
    # settings rather than hardcoding it in application code.
    requirements_qualification_step_id: str = "analyze-requirements"

    # Ids of the workflow steps that must be re-executed (alongside
    # build-solution) whenever a customer applies selected fixes from the
    # Security Assessment/Test Generation agents' own findings, so those
    # gates are re-evaluated against the regenerated build rather than
    # showing stale results.
    gated_fix_step_ids: tuple[str, ...] = (
        "security-assessment",
        "test-generation",
    )

    # --- CORS -------------------------------------------------------------------
    # Comma-separated list of browser origins allowed to call this API (e.g. the
    # Genie frontend's Static Web App hostname). Empty by default - no
    # cross-origin browser access - so this must be explicitly configured per
    # deployment; never hardcoded to a real environment hostname in source.
    cors_allowed_origins: str = ""

    # --- Deploy & Launch pipeline (real Azure automation for each mission's
    # generated build) -----------------------------------------------------------
    # The Azure resource group, container registry, Container Apps managed
    # environment, storage account, and region every mission's generated
    # backend/frontend are deployed into. All optional and unset by default -
    # never hardcoded to a real subscription/resource - so a deployment must
    # explicitly configure these before the Deploy & Launch pipeline's real
    # steps (provision Foundry agents, deploy backend, deploy frontend) can
    # run; see app.deploy_launch.pipeline_service.
    deployment_resource_group: str | None = None
    deployment_acr_name: str | None = None
    deployment_container_apps_environment_id: str | None = None
    deployment_storage_account_name: str | None = None
    deployment_location: str | None = None
    # Local filesystem root the pipeline materializes each mission's generated
    # build under (one subdirectory per pipeline run id) before packaging it
    # for ACR/Storage upload - never a customer-specific path in source.
    deployment_build_workspace_root: Path = Path("var/deploy-launch-builds")

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    # --- Externalized configuration roots (never hold secrets or business data) -
    config_root: Path = Path("config")
    agents_config_dir: str = "agents"
    prompts_config_dir: str = "prompts"
    workflows_config_dir: str = "workflows"
    policies_config_dir: str = "policies"

    @property
    def agents_path(self) -> Path:
        return self.config_root / self.agents_config_dir

    @property
    def prompts_path(self) -> Path:
        return self.config_root / self.prompts_config_dir

    @property
    def workflows_path(self) -> Path:
        return self.config_root / self.workflows_config_dir

    @property
    def policies_path(self) -> Path:
        return self.config_root / self.policies_config_dir

    @field_validator(
        "azure_foundry_endpoint",
        "azure_speech_endpoint",
        "key_vault_uri",
        "memory_store_endpoint",
        "lineage_store_endpoint",
        "azure_subscription_id",
        "deployment_resource_group",
        "deployment_acr_name",
        "deployment_container_apps_environment_id",
        "deployment_storage_account_name",
        "deployment_location",
        mode="after",
    )
    @classmethod
    def _blank_string_to_none(cls, value: str | None) -> str | None:
        if value is not None and value.strip() == "":
            return None
        return value


def get_settings() -> Settings:
    """Build a fresh ``Settings`` instance from the current environment.

    A fresh instance (rather than a cached singleton) is returned so that
    tests and multi-environment tooling can freely vary environment
    variables between calls without stale state leaking across cases.
    """

    return Settings()
