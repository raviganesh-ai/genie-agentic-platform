"""Validates the prompt template registry configuration can be loaded successfully.

Full prompt resolution and execution is implemented alongside
AzureAgentGateway in Phase 3; this validator enforces the Validation
Requirements in ``.github/copilot-instructions.md`` by ensuring the
prompt registry under ``Settings.prompts_path`` exists, parses, and
validates.
"""
from __future__ import annotations

from app.config.settings import Settings
from app.prompts.registry import PromptRegistry, PromptRegistryError
from app.validation.base import ValidationResult


class PromptTemplateValidator:
    """Fails closed if the prompt template registry is missing, empty, or invalid."""

    name = "PromptTemplateValidator"

    def validate(self, settings: Settings) -> ValidationResult:
        try:
            PromptRegistry.load(settings.prompts_path)
        except PromptRegistryError as exc:
            return ValidationResult.fail(self.name, [str(exc)])
        return ValidationResult.ok(self.name)
