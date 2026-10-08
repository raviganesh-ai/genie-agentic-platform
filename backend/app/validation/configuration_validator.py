"""Validates core configuration values are present and well-formed."""
from __future__ import annotations

from app.config.settings import Settings
from app.validation.base import ValidationResult

_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class ConfigurationValidator:
    """Fails closed if required base configuration is missing or malformed."""

    name = "ConfigurationValidator"

    def validate(self, settings: Settings) -> ValidationResult:
        errors: list[str] = []

        if not settings.service_name.strip():
            errors.append("service_name must not be empty.")

        if settings.log_level.upper() not in _VALID_LOG_LEVELS:
            errors.append(
                f"log_level '{settings.log_level}' must be one of {sorted(_VALID_LOG_LEVELS)}."
            )

        if not settings.config_root:
            errors.append("config_root must be set.")
        elif not settings.config_root.is_dir():
            errors.append(f"config_root '{settings.config_root}' does not exist.")

        if not settings.default_llm.strip():
            errors.append("default_llm must not be empty.")

        if settings.environment == "production":
            if not settings.prototype_api_gateway_enabled:
                errors.append("prototype_api_gateway_enabled must be true in production.")
            # Deploy & Launch's backend/frontend deployment factories silently
            # fall back to Null*DeploymentService stand-ins (fake "localhost"
            # URLs, no real Azure resources touched) whenever these are unset -
            # a real production deployment must never allow that; see
            # app.deploy_launch.backend_deployment_service and
            # frontend_deployment_service's create_*_deployment_service factories.
            if not settings.azure_subscription_id:
                errors.append("azure_subscription_id is required in production.")
            if not settings.deployment_resource_group:
                errors.append("deployment_resource_group is required in production.")
            if not settings.deployment_acr_name:
                errors.append("deployment_acr_name is required in production.")
            if not settings.deployment_acr_agent_pool_name:
                errors.append("deployment_acr_agent_pool_name is required in production.")
            if not settings.deployment_container_apps_environment_id:
                errors.append(
                    "deployment_container_apps_environment_id is required in production."
                )
            if not settings.deployment_location:
                errors.append("deployment_location is required in production.")

        if settings.prototype_api_gateway_enabled:
            if not settings.prototype_api_gateway_publisher_email:
                errors.append(
                    "prototype_api_gateway_publisher_email is required when the prototype "
                    "API gateway is enabled."
                )
            if not settings.prototype_api_gateway_publisher_name:
                errors.append(
                    "prototype_api_gateway_publisher_name is required when the prototype "
                    "API gateway is enabled."
                )
        if settings.github_mcp_enabled:
            if not settings.github_mcp_endpoint:
                errors.append("github_mcp_endpoint is required when GitHub MCP is enabled.")
            elif not settings.github_mcp_endpoint.startswith("https://"):
                errors.append("github_mcp_endpoint must use HTTPS.")
            if not settings.github_mcp_token_env_var:
                errors.append(
                    "github_mcp_token_env_var is required when GitHub MCP is enabled."
                )

        # Static/administrator-token IQ providers (unchanged authorization
        # model - Foundry IQ and Foundry MCP have no confirmed delegated
        # path, see docs/architecture/genie-sas-microsoft-iq.md).
        static_iq_providers = (
            (
                "foundry_iq",
                settings.foundry_iq_enabled,
                settings.foundry_iq_mcp_endpoint,
                settings.foundry_iq_token_env_var,
                settings.foundry_iq_retrieve_tool,
            ),
            (
                "foundry_mcp",
                settings.foundry_mcp_enabled,
                settings.foundry_mcp_endpoint,
                settings.foundry_mcp_token_env_var,
                settings.foundry_mcp_retrieve_tool,
            ),
        )
        for provider, enabled, endpoint, token_env_var, retrieve_tool in static_iq_providers:
            if not enabled:
                continue
            endpoint_field = (
                "foundry_mcp_endpoint" if provider == "foundry_mcp" else f"{provider}_mcp_endpoint"
            )
            if not endpoint:
                errors.append(f"{endpoint_field} is required when {provider} is enabled.")
            elif not endpoint.startswith("https://"):
                errors.append(f"{endpoint_field} must use HTTPS.")
            if not token_env_var:
                errors.append(f"{provider}_token_env_var is required when {provider} is enabled.")
            if not retrieve_tool:
                errors.append(f"{provider}_retrieve_tool is required when {provider} is enabled.")

        # Delegated Microsoft Entra OAuth IQ providers (Work IQ / Fabric IQ).
        # These have no token_env_var - they share one Genie-SaS OAuth
        # client registration, validated once below.
        delegated_iq_providers = (
            ("work_iq", settings.work_iq_enabled, settings.work_iq_mcp_endpoint, settings.work_iq_retrieve_tool),
            ("fabric_iq", settings.fabric_iq_enabled, settings.fabric_iq_mcp_endpoint, settings.fabric_iq_retrieve_tool),
        )
        any_delegated_iq_enabled = False
        for provider, enabled, endpoint, retrieve_tool in delegated_iq_providers:
            if not enabled:
                continue
            any_delegated_iq_enabled = True
            if not endpoint:
                errors.append(f"{provider}_mcp_endpoint is required when {provider} is enabled.")
            elif not endpoint.startswith("https://"):
                errors.append(f"{provider}_mcp_endpoint must use HTTPS.")
            if not retrieve_tool:
                errors.append(f"{provider}_retrieve_tool is required when {provider} is enabled.")
        if settings.fabric_iq_enabled and not settings.fabric_iq_scopes:
            errors.append(
                "fabric_iq_scopes is required when fabric_iq is enabled (Microsoft documents "
                "the required Power BI Service API permissions, but the exact delegated scope "
                "strings depend on this app registration's exposed permissions)."
            )
        if any_delegated_iq_enabled:
            if not settings.iq_oauth_tenant_id:
                errors.append("iq_oauth_tenant_id is required when a delegated IQ provider is enabled.")
            if not settings.iq_oauth_client_id:
                errors.append("iq_oauth_client_id is required when a delegated IQ provider is enabled.")
            if not settings.iq_oauth_client_secret_env_var:
                errors.append(
                    "iq_oauth_client_secret_env_var is required when a delegated IQ provider is "
                    "enabled."
                )
            if not settings.iq_oauth_redirect_uri:
                errors.append(
                    "iq_oauth_redirect_uri is required when a delegated IQ provider is enabled."
                )
            elif not settings.iq_oauth_redirect_uri.startswith("https://"):
                errors.append("iq_oauth_redirect_uri must use HTTPS.")
            if settings.environment == "production" and not settings.iq_delegated_oauth_allowed_in_production:
                errors.append(
                    "iq_delegated_oauth_allowed_in_production must be true to enable a delegated "
                    "IQ provider in production."
                )

        if errors:
            return ValidationResult.fail(self.name, errors)
        return ValidationResult.ok(self.name)
