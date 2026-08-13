"""Validates the governance provider and policy configuration.

Fails closed at startup when the configured governance provider name is
unrecognized, or when required governance/approval policy configuration
is missing or invalid.
"""
from __future__ import annotations

from app.config.settings import Settings
from app.governance.approval_service import ApprovalPolicyError, load_approval_policy
from app.governance.governance_service import GovernancePolicyError, load_governance_policy
from app.validation.base import ValidationResult

_VALID_PROVIDERS = {"local", "agent365"}


class GovernanceProviderValidator:
    """Fails closed on any unsafe or incomplete governance configuration."""

    name = "GovernanceProviderValidator"

    def validate(self, settings: Settings) -> ValidationResult:
        errors: list[str] = []

        provider = settings.governance_provider
        if provider not in _VALID_PROVIDERS:
            errors.append(
                f"governance_provider '{provider}' must be one of {sorted(_VALID_PROVIDERS)}."
            )

        try:
            load_governance_policy(settings.policies_path)
        except GovernancePolicyError as exc:
            errors.append(str(exc))

        try:
            load_approval_policy(settings.policies_path)
        except ApprovalPolicyError as exc:
            errors.append(str(exc))

        if errors:
            return ValidationResult.fail(self.name, errors)
        return ValidationResult.ok(self.name)
