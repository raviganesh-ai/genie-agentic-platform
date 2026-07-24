"""Executes all fail-closed startup validators in the required order."""
from __future__ import annotations

import logging

from app.config.settings import Settings
from app.validation.agent_registry_validator import AgentRegistryValidator
from app.validation.base import StartupValidationError, StartupValidator, ValidationResult
from app.validation.configuration_validator import ConfigurationValidator
from app.validation.governance_provider_validator import GovernanceProviderValidator
from app.validation.memory_policy_validator import MemoryPolicyValidator
from app.validation.no_hardcoding_validator import NoHardcodingValidator
from app.validation.production_safety_validator import ProductionSafetyValidator
from app.validation.prompt_template_validator import PromptTemplateValidator
from app.validation.provider_mode_validator import ProviderModeValidator
from app.validation.runtime_version_validator import RuntimeVersionValidator
from app.validation.workflow_registry_validator import WorkflowRegistryValidator

logger = logging.getLogger(__name__)


def default_validators() -> list[StartupValidator]:
    """Return the ordered list of validators required before startup.

    Order matches the "Required validators" list in
    ``.github/copilot-instructions.md``.
    """

    return [
        RuntimeVersionValidator(),
        ConfigurationValidator(),
        ProviderModeValidator(),
        ProductionSafetyValidator(),
        AgentRegistryValidator(),
        WorkflowRegistryValidator(),
        PromptTemplateValidator(),
        MemoryPolicyValidator(),
        GovernanceProviderValidator(),
        NoHardcodingValidator(),
    ]


class StartupValidationRunner:
    """Runs every required validator and fails closed on any failure."""

    def __init__(self, validators: list[StartupValidator] | None = None) -> None:
        self._validators = validators if validators is not None else default_validators()

    def run(self, settings: Settings) -> list[ValidationResult]:
        """Run all validators and return their results without raising."""

        results: list[ValidationResult] = []
        for validator in self._validators:
            result = validator.validate(settings)
            results.append(result)
            if result.passed:
                logger.info("Startup validation passed: %s", validator.name)
            else:
                for issue in result.issues:
                    logger.error(
                        "Startup validation failed: [%s] %s", validator.name, issue.message
                    )
        return results

    def run_or_raise(self, settings: Settings) -> list[ValidationResult]:
        """Run all validators; raise ``StartupValidationError`` on any failure.

        No fallback behavior is attempted here: a failure must propagate so
        the application can fail closed, per the Fail Closed Requirements in
        ``.github/copilot-instructions.md``.
        """

        results = self.run(settings)
        if any(not result.passed for result in results):
            raise StartupValidationError(results)
        return results
