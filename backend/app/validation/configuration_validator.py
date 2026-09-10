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
            if not settings.entra_tenant_id:
                errors.append("entra_tenant_id is required when the prototype API gateway is enabled.")
            if (
                settings.prototype_authentication_mode == "per_prototype"
                and not settings.prototype_test_principal_client_id
            ):
                errors.append(
                    "prototype_test_principal_client_id is required when the prototype API "
                    "gateway uses per-prototype authentication."
                )

        if errors:
            return ValidationResult.fail(self.name, errors)
        return ValidationResult.ok(self.name)
