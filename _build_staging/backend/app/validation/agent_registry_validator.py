"""Validates the agent registry configuration can be loaded successfully.

Full agent execution (the Azure AI Foundry gateway) is implemented in
Phase 3; this validator enforces the Validation Requirements in
``.github/copilot-instructions.md`` by ensuring the agent registry under
``Settings.agents_path`` exists, parses, and validates.
"""
from __future__ import annotations

from app.agents.registry import AgentRegistry, AgentRegistryError
from app.config.settings import Settings
from app.validation.base import ValidationResult


class AgentRegistryValidator:
    """Fails closed if the agent registry is missing, empty, or invalid."""

    name = "AgentRegistryValidator"

    def validate(self, settings: Settings) -> ValidationResult:
        try:
            AgentRegistry.load(settings.agents_path, default_llm=settings.default_llm)
        except AgentRegistryError as exc:
            return ValidationResult.fail(self.name, [str(exc)])
        return ValidationResult.ok(self.name)
