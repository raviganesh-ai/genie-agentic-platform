"""Enforces that production mode never runs mock/local agents or synthetic data.

See "Production Agent Rules" and "Fail Closed Requirements" in
``.github/copilot-instructions.md``.
"""
from __future__ import annotations

from app.config.settings import Settings
from app.validation.base import ValidationResult


class ProductionSafetyValidator:
    """Fails closed if production mode is misconfigured to allow unsafe execution."""

    name = "ProductionSafetyValidator"

    def validate(self, settings: Settings) -> ValidationResult:
        if settings.provider_mode != "production":
            return ValidationResult.ok(self.name)

        errors: list[str] = []
        if settings.allow_mock_agents:
            errors.append("allow_mock_agents must be False in production.")
        if settings.allow_local_agents:
            errors.append("allow_local_agents must be False in production.")
        if settings.use_synthetic_data:
            errors.append("use_synthetic_data must be False in production.")
        if not settings.azure_foundry_endpoint:
            errors.append("azure_foundry_endpoint is required in production.")
        if not settings.azure_foundry_project_name:
            errors.append("azure_foundry_project_name is required in production.")
        if not settings.key_vault_uri:
            errors.append("key_vault_uri is required in production.")
        if not settings.entra_tenant_id:
            errors.append("entra_tenant_id is required in production.")
        if not settings.entra_client_id:
            errors.append("entra_client_id is required in production.")
        if not settings.mise_endpoint:
            errors.append("mise_endpoint is required for MISE token validation in production.")

        if errors:
            return ValidationResult.fail(self.name, errors)
        return ValidationResult.ok(self.name)
