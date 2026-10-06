"""Fail-closed production provider and persistence checks."""
from __future__ import annotations

from app.config.settings import Settings
from app.validation.base import ValidationResult


class ProductionSafetyValidator:
    """Rejects development-only providers and volatile storage in production."""

    name = "ProductionSafetyValidator"

    def validate(self, settings: Settings) -> ValidationResult:
        if settings.environment != "production":
            return ValidationResult.ok(self.name)

        errors: list[str] = []
        if settings.governance_provider != "agent365":
            errors.append("governance_provider must be 'agent365' in production.")
        if settings.memory_store_backend != "cosmos_db":
            errors.append("memory_store_backend must be 'cosmos_db' in production.")
        if settings.lineage_store_backend != "cosmos_db":
            errors.append("lineage_store_backend must be 'cosmos_db' in production.")
        if not settings.key_vault_uri:
            errors.append("key_vault_uri is required in production.")
        if not settings.azure_subscription_id:
            errors.append("azure_subscription_id is required in production.")
        if not settings.production_resource_group:
            errors.append("production_resource_group is required in production.")
        if not settings.deployment_location:
            errors.append("deployment_location is required in production.")
        if not settings.auth_enabled:
            errors.append("auth_enabled must be true in production.")
        else:
            if not settings.auth_token_signing_key_env_var:
                errors.append("auth_token_signing_key_env_var is required when auth_enabled is true.")
            if not settings.auth_users_env_var:
                errors.append("auth_users_env_var is required when auth_enabled is true.")
        if errors:
            return ValidationResult.fail(self.name, errors)
        return ValidationResult.ok(self.name)
