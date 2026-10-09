"""Strongly typed, externally sourced application settings.

No configuration value in this module may be hardcoded to a real endpoint,
credential, tenant, subscription, or customer value. Settings are sourced
exclusively from environment variables (optionally via a local ``.env``
file), per the Configuration Rules in ``.github/copilot-instructions.md``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

GovernanceProviderName = Literal["local", "agent365"]
Environment = Literal["development", "test", "production"]
MemoryStoreBackend = Literal["in_memory", "cosmos_db"]

# Default LLM used by every agent unless it (or the caller) supplies an
# explicit override. Not a secret or endpoint - a plain application default,
# externally overridable via GENIE_DEFAULT_LLM. "gpt-5-mini" is used because
# it is the EXACT Cognitive Services deployment name provisioned on the real
# Foundry account - Foundry agent run-time model resolution requires an
# exact deployment-name match (unlike agent create/update, which silently
# accepts other string forms and only fails when a run is actually
# attempted). There is no "gpt-5.1-mini" deployment provisioned on this
# Foundry account (only "gpt-5-1" for gpt-5.1 and "gpt-5-mini" for the base
# gpt-5-mini model) - never invent a deployment name that does not exist.
# Anthropic Claude models require a Marketplace subscription and, as of this
# writing, this subscription has a default quota of 0 for every Claude SKU.
DEFAULT_LLM = "gpt-5-mini"


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

    # --- Execution safety -------------------------------------------------------
    # Legacy flags remain parseable so unsafe deployment manifests fail with
    # explicit validation errors instead of being silently ignored. Genie
    # never permits these modes; all agent execution uses Azure AI Foundry.
    governance_provider: GovernanceProviderName = "local"
    allow_mock_agents: bool = False
    allow_local_agents: bool = False
    use_synthetic_data: bool = False

    # --- Azure AI Foundry ---------------------------------------------------------
    azure_foundry_endpoint: str | None = None
    azure_foundry_project_name: str | None = None
    # Resource group that actually contains the Foundry account identified by
    # azure_foundry_endpoint - needed for ARM-level lookups against that
    # account (e.g. listing model deployments; see
    # app.services.model_catalog_service). This is deliberately a separate
    # setting from deployment_resource_group: a Genie-SaS environment may
    # provision its own dedicated infrastructure (Cosmos DB, Key Vault,
    # Container Apps, ...) while intentionally reusing an existing Foundry
    # project that lives in a different resource group, to avoid the cost
    # and delay of re-registering every Foundry agent. Falls back to
    # deployment_resource_group when unset, which preserves prior behavior
    # for environments where the Foundry account and the Deploy & Launch
    # target resource group are the same.
    azure_foundry_resource_group: str | None = None
    azure_retail_prices_endpoint: str = "https://prices.azure.com/api/retail/prices"
    # A real Foundry agent run can legitimately take several minutes for a
    # large generation (e.g. a Build Agent component synthesizing an
    # orchestrator that coordinates many specialist agents), but an
    # unbounded wait on a genuinely hung/stalled call leaves a mission
    # stuck forever with no way to recover - FoundryAgentProvider.run/
    # run_stream enforce this as a hard ceiling, raising
    # FoundryUnavailableError (the existing fail-closed path, already
    # retryable via resume_workflow) once exceeded, rather than blocking
    # indefinitely.
    foundry_agent_run_timeout_seconds: float = Field(default=900, gt=0, le=1800)

    # --- Azure Content Understanding (customer evidence ingestion) --------------
    # A dedicated account endpoint can be supplied. When omitted, production
    # derives the account root from azure_foundry_endpoint so the existing
    # AIServices resource can host both Foundry agents and Content Understanding.
    azure_content_understanding_endpoint: str | None = None
    content_understanding_analyzer_id: str = "prebuilt-documentSearch"
    content_understanding_api_version: Literal["2025-11-01"] = "2025-11-01"
    content_understanding_processing_location: Literal[
        "geography", "dataZone", "global"
    ] = "geography"
    content_understanding_timeout_seconds: float = Field(default=300, gt=0, le=1800)
    content_understanding_poll_interval_seconds: float = Field(default=2, ge=0.1, le=30)

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
    memory_store_database_name: str = "genie"
    memory_store_container_name: str = "memory"

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

    # --- GitHub MCP repository evidence -----------------------------------------
    # Uses the ministry-demo integration pattern: Genie calls an externally
    # hosted GitHub MCP server with an administrator-provisioned bearer token.
    # The endpoint and token environment-variable name are externalized. The
    # deployment must populate that variable through a Key Vault-backed
    # Container Apps/App Service secret reference; the token is never accepted
    # from the browser or represented as a typed application setting.
    github_mcp_enabled: bool = False
    github_mcp_endpoint: str | None = None
    github_mcp_token_env_var: str | None = None
    github_mcp_timeout_seconds: float = Field(default=30, gt=0, le=120)
    repository_assessment_max_files: int = Field(default=1000, ge=1, le=10000)
    repository_assessment_max_depth: int = Field(default=20, ge=1, le=100)
    repository_assessment_max_source_bytes: int = Field(
        default=1_000_000, ge=1024, le=10_000_000
    )

    # --- Microsoft Learn MCP (grounded Well-Architected / Azure docs Q&A) ------
    # The public, unauthenticated Microsoft Learn MCP server - confirmed live
    # (microsoft_docs_search/microsoft_docs_fetch) during development, not
    # merely assumed from documentation. Used so WellArchitectedQaService can
    # answer only from real, retrieved learn.microsoft.com content (cited by
    # URL) instead of the model's own unverified trained knowledge - no new
    # Azure resource or secret required since this endpoint requires no auth.
    well_architected_qa_enabled: bool = True
    microsoft_learn_mcp_endpoint: str = "https://learn.microsoft.com/api/mcp"
    microsoft_learn_mcp_timeout_seconds: float = Field(default=20, gt=0, le=120)
    well_architected_max_search_results: int = Field(default=5, ge=1, le=10)
    well_architected_max_fetched_documents: int = Field(default=2, ge=0, le=5)

    # --- IQ evidence providers -------------------------------------------------
    # Provider-specific tool names and query argument names are externalized
    # because Genie validates the real MCP capability contract at runtime
    # instead of inventing undocumented Work IQ, Foundry IQ, Fabric IQ, or
    # Foundry MCP APIs.
    #
    # Work IQ and Fabric IQ are DELEGATED providers: Microsoft's own
    # documentation confirms neither supports application-only/
    # service-principal authentication (see
    # docs/architecture/genie-sas-microsoft-iq.md "Official Microsoft
    # references"), so they have no static token_env_var - see the
    # "Delegated Microsoft Entra OAuth" block below for their auth config.
    work_iq_enabled: bool = False
    work_iq_mcp_endpoint: str | None = None
    work_iq_retrieve_tool: str | None = None
    work_iq_query_argument: str = "query"
    foundry_iq_enabled: bool = False
    foundry_iq_mcp_endpoint: str | None = None
    foundry_iq_token_env_var: str | None = None
    foundry_iq_retrieve_tool: str | None = None
    foundry_iq_query_argument: str = "query"
    fabric_iq_enabled: bool = False
    fabric_iq_mcp_endpoint: str | None = None
    fabric_iq_retrieve_tool: str | None = None
    fabric_iq_query_argument: str = "query"
    # Microsoft's Foundry MCP server (https://mcp.ai.azure.com) is currently
    # documented only for interactive developer clients (VS Code + Entra ID
    # sign-in) - see docs/architecture/genie-sas-microsoft-iq.md "Microsoft
    # Foundry MCP". No service-principal/application-only auth path is
    # published, so this stays disabled by default until an administrator
    # provisions a delegated token out-of-band, same as Foundry IQ.
    foundry_mcp_enabled: bool = False
    foundry_mcp_endpoint: str | None = None
    foundry_mcp_token_env_var: str | None = None
    foundry_mcp_retrieve_tool: str | None = None
    foundry_mcp_query_argument: str = "query"
    iq_mcp_timeout_seconds: float = Field(default=60, gt=0, le=300)

    # --- Delegated Microsoft Entra OAuth (Work IQ / Fabric IQ only) -------------
    # One confidential-client Entra app registration Genie-SaS itself owns,
    # used ONLY to obtain a per-session, per-user delegated token for these
    # two IQ providers via authorization-code + PKCE - see
    # docs/architecture/genie-sas-microsoft-iq.md. This does not reintroduce
    # interactive sign-in for Genie-SaS itself (see README.md
    # "Authentication"); normal Genie usage remains anonymous. Only a user
    # who explicitly clicks "Connect Microsoft 365" goes through this flow.
    iq_oauth_tenant_id: str | None = None
    iq_oauth_client_id: str | None = None
    iq_oauth_client_secret_env_var: str | None = None
    iq_oauth_redirect_uri: str | None = None
    iq_oauth_state_ttl_seconds: float = Field(default=600, gt=0, le=3600)
    # Space-separated delegated OAuth scopes. Work IQ has one confirmed,
    # documented scope and falls back to it when unset (see
    # app.iq.microsoft_resource_registry); Fabric IQ's exact scope strings
    # depend on how the administrator exposed the Power BI Service API
    # permissions in this app registration, so it has no default.
    work_iq_scopes: str | None = None
    fabric_iq_scopes: str | None = None
    # Safe environment restriction (Phase 13): even with full OAuth config
    # present, delegated Work IQ/Fabric IQ must not be enabled in production
    # without this additional, separately-set flag.
    iq_delegated_oauth_allowed_in_production: bool = False

    @property
    def work_iq_scopes_list(self) -> tuple[str, ...] | None:
        return tuple(self.work_iq_scopes.split()) if self.work_iq_scopes else None

    @property
    def fabric_iq_scopes_list(self) -> tuple[str, ...] | None:
        return tuple(self.fabric_iq_scopes.split()) if self.fabric_iq_scopes else None

    # --- First-party platform authentication (replaces the single shared
    # "genie-internal-user" identity) ---------------------------------------
    # Genie-SaS itself was deliberately made anonymous in September 2026 (see
    # README.md "Authentication"), relying on the platform APIM/network
    # boundary alone. Once the platform became reachable beyond a fully
    # trusted private network, that left every session readable by anyone
    # who could reach the gateway with no credential at all (an
    # unauthenticated IDOR). This does not reintroduce Microsoft Entra ID -
    # it is a small, self-contained bearer-token login so real per-account
    # ownership checks (see app.services.session_service) become meaningful
    # again, without standing up an external identity provider.
    #
    # Disabled by default (dev/test keep today's single deterministic
    # identity so the existing test suite needs no Authorization header);
    # ProductionSafetyValidator fails startup if this is not enabled, or
    # enabled without the two secrets below, in production.
    auth_enabled: bool = False
    # Name of the environment variable holding the HMAC-SHA256 signing key
    # for issued bearer tokens (never the key value itself - see
    # iq_oauth_client_secret_env_var for the same indirection pattern).
    auth_token_signing_key_env_var: str | None = None
    auth_token_ttl_seconds: float = Field(default=3600, gt=0, le=86400)
    # Name of the environment variable holding the newline-separated
    # "username:pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>" user
    # records (see app.security.password_hashing). Supports multiple real
    # accounts without a database.
    auth_users_env_var: str | None = None

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
    deployment_acr_agent_pool_name: str | None = None
    deployment_container_apps_environment_id: str | None = None
    deployment_storage_account_name: str | None = None
    deployment_location: str | None = None
    production_resource_group: str | None = None
    production_canary_weight_percent: int = Field(default=10, ge=1, le=50)
    production_health_timeout_seconds: float = Field(default=30, gt=0, le=300)
    prototype_api_gateway_enabled: bool = False
    prototype_api_gateway_publisher_email: str | None = None
    prototype_api_gateway_publisher_name: str | None = None
    prototype_api_gateway_sku_name: Literal["StandardV2", "PremiumV2"] = "StandardV2"
    prototype_api_gateway_capacity: int = Field(default=1, ge=1, le=10)
    # Genie's own shared virtual network (holding the private-endpoint-only
    # shared resources every mission's generated backend must reach - the
    # shared Container Registry, the shared Azure AI Foundry account, etc.).
    # When configured, every prototype's own isolated VNet is peered to this
    # shared VNet and linked to the named shared private DNS zones below, so
    # the mission's generated backend/agents can resolve and reach those
    # shared resources over genuinely private networking - never by opening
    # public network access on the shared resource (see a real, observed
    # incident: a mission's Identity Context Agent failing with "(403)
    # Public access is disabled" when calling the shared Foundry account,
    # because the mission's own VNet had no private path to it at all).
    # Optional and unset by default - never hardcoded to a real
    # subscription/resource - so a deployment must explicitly configure this
    # before Deploy & Launch peers any mission's network to it.
    shared_vnet_resource_id: str | None = None
    # The resource group that owns both the shared VNet above and every
    # shared private DNS zone named below (all must live together, matching
    # Genie's own standard shared-infrastructure resource group).
    shared_network_resource_group: str | None = None
    # Comma-separated shared private DNS zone names (already linked to the
    # shared VNet above) that a mission's own VNet must also link to, so DNS
    # resolution of each shared resource's private endpoint FQDN works from
    # inside the mission's own, separately-peered VNet. Azure Private DNS
    # Zones require an explicit VNet link per resolving VNet; peering alone
    # only provides IP-level reachability, not DNS resolution.
    shared_private_dns_zone_names: str = (
        "privatelink.azurecr.io,"
        "privatelink.cognitiveservices.azure.com,"
        "privatelink.openai.azure.com,"
        "privatelink.services.ai.azure.com"
    )
    deployment_fidelity_max_repair_attempts: int = 3
    deployment_fidelity_min_coverage_percent: float = Field(default=90.0, gt=0, le=100)
    # How long Requirement Validation's real pytest subprocess is
    # allowed to run before being killed. This suite executes real black-box
    # HTTP acceptance tests against a live deployed mission prototype (one
    # test per approved requirement) - not fast in-process unit tests - so it
    # scales with the number of approved requirements. A too-short timeout
    # kills the whole pytest process before it can write any JUnit XML at
    # all, which discards every real pass/fail outcome and misreports every
    # single requirement as if its test didn't exist, rather than surfacing
    # the real "the suite didn't finish in time" cause.
    deployment_test_execution_timeout_seconds: int = 300
    prototype_default_ttl_days: int = Field(default=7, ge=1, le=90)
    prototype_max_active_per_owner: int = Field(default=0, ge=0, le=20)
    prototype_cleanup_interval_seconds: int = Field(default=3600, ge=60, le=86400)
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
        "azure_foundry_resource_group",
        "azure_content_understanding_endpoint",
        "azure_speech_endpoint",
        "key_vault_uri",
        "github_mcp_endpoint",
        "github_mcp_token_env_var",
        "work_iq_mcp_endpoint",
        "work_iq_retrieve_tool",
        "foundry_iq_mcp_endpoint",
        "foundry_iq_token_env_var",
        "foundry_iq_retrieve_tool",
        "fabric_iq_mcp_endpoint",
        "fabric_iq_retrieve_tool",
        "foundry_mcp_endpoint",
        "foundry_mcp_token_env_var",
        "foundry_mcp_retrieve_tool",
        "iq_oauth_tenant_id",
        "iq_oauth_client_id",
        "iq_oauth_client_secret_env_var",
        "iq_oauth_redirect_uri",
        "work_iq_scopes",
        "fabric_iq_scopes",
        "memory_store_endpoint",
        "lineage_store_endpoint",
        "azure_subscription_id",
        "deployment_resource_group",
        "deployment_acr_name",
        "deployment_container_apps_environment_id",
        "deployment_storage_account_name",
        "deployment_location",
        "production_resource_group",
        "prototype_api_gateway_publisher_email",
        "prototype_api_gateway_publisher_name",
        "shared_vnet_resource_id",
        "shared_network_resource_group",
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
