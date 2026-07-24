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

ProviderMode = Literal["local", "production"]
GovernanceProviderName = Literal["local", "agent365"]
Environment = Literal["development", "test", "production"]
MemoryStoreBackend = Literal["in_memory", "cosmos_db"]

# Default LLM used by every agent unless it (or the caller) supplies an
# explicit override. Not a secret or endpoint - a plain application default,
# externally overridable via GENIE_DEFAULT_LLM. gpt-5.1 is used because it is
# sold directly by Azure (no Azure Marketplace subscription/quota required);
# Anthropic Claude models require a Marketplace subscription and, as of this
# writing, this subscription has a default quota of 0 for every Claude SKU.
DEFAULT_LLM = "gpt-5.1"


class Settings(BaseSettings):
    """Strongly typed application settings.

    All values are externally configured via environment variables prefixed
    with ``GENIE_`` (e.g. ``GENIE_PROVIDER_MODE``). See ``.env.example`` for
    the full list of supported variables.
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

    # --- Execution mode ---------------------------------------------------------
    provider_mode: ProviderMode = "local"
    governance_provider: GovernanceProviderName = "local"

    # --- Production safety flags (must be False when provider_mode=production) -
    allow_mock_agents: bool = True
    allow_local_agents: bool = True
    use_synthetic_data: bool = True

    # --- Azure AI Foundry ---------------------------------------------------------
    azure_foundry_endpoint: str | None = None
    azure_foundry_project_name: str | None = None

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
    entra_tenant_id: str | None = None
    entra_client_id: str | None = None

    # --- Customer-experience (cx) session tokens ---------------------------------
    # Signs the short-lived, session-scoped access tokens used by the
    # customer-facing generated-prototype surface (app/api/cx.py) - a
    # distinct, least-privilege identity from internal Mission Control users
    # (see app.security.token_validator). Sourced from Key Vault in
    # production (via a Container App Key Vault secret reference), never
    # hardcoded. See app.security.cx_tokens.create_cx_token_service for the
    # fail-closed resolution contract.
    cx_token_signing_key: str | None = None
    cx_token_ttl_seconds: int = 3600
    # Id of the workflow step (config/workflows/*.yaml) whose output_text is
    # the generated customer prototype HTML, served by app/api/cx.py.
    # Mirrors debugging_workflow_id's pattern of naming a config entity from
    # settings rather than hardcoding it in application code.
    cx_prototype_step_id: str = "generate-prototype"

    # --- CORS -------------------------------------------------------------------
    # Comma-separated list of browser origins allowed to call this API (e.g. the
    # Genie frontend's Static Web App hostname). Empty by default - no
    # cross-origin browser access - so this must be explicitly configured per
    # deployment; never hardcoded to a real environment hostname in source.
    cors_allowed_origins: str = ""

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
        "cx_token_signing_key",
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
