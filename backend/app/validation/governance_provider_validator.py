"""Validates the governance provider and policy configuration is production-safe.

Enforces the "FAIL CLOSED" requirements for Phase 5: production startup
must fail when the governance provider is unavailable/unsafe, required
governance policies are missing or invalid, lineage storage is not
durably configured, or approval policy configuration is missing/invalid.
"""
from __future__ import annotations

from app.config.settings import Settings
from app.governance.approval_service import ApprovalPolicyError, load_approval_policy
from app.governance.governance_service import GovernancePolicyError, load_governance_policy
from app.validation.base import ValidationResult

_VALID_PROVIDERS = {"local", "agent365"}


class GovernanceProviderValidator:
    """Fails closed on any unsafe or incomplete Phase 5 governance configuration."""

    name = "GovernanceProviderValidator"

    def validate(self, settings: Settings) -> ValidationResult:
        errors: list[str] = []

        provider = settings.governance_provider
        if provider not in _VALID_PROVIDERS:
            errors.append(
                f"governance_provider '{provider}' must be one of {sorted(_VALID_PROVIDERS)}."
            )
        elif settings.provider_mode == "production" and provider != "agent365":
            errors.append(
                f"governance_provider must be 'agent365' in production; got '{provider}'."
            )

        try:
            load_governance_policy(settings.policies_path)
        except GovernancePolicyError as exc:
            errors.append(str(exc))

        try:
            load_approval_policy(settings.policies_path)
        except ApprovalPolicyError as exc:
            errors.append(str(exc))

        if settings.provider_mode == "production":
            if settings.lineage_store_backend == "in_memory":
                errors.append(
                    "lineage_store_backend must not be 'in_memory' in production; "
                    "configure a durable backend."
                )
            if not settings.lineage_store_endpoint:
                errors.append("lineage_store_endpoint is required in production.")

        if errors:
            return ValidationResult.fail(self.name, errors)
        return ValidationResult.ok(self.name)
