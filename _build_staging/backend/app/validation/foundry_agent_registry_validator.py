"""Foundry agent registry validator (Phase 10A).

Configuration-only (no network) check that every enabled agent that will
execute through ``AzureAgentGateway`` carries the metadata Foundry
provisioning/synchronization requires: a Foundry agent reference, version,
governance policy id, resolvable prompt template reference, deployment
reference, ownership, and non-empty memory scope.

Genie is a personal dev/demo deployment with no separate production tier
that requires this metadata to be fully populated, so ``validate`` is
currently a permanent no-op; it is kept as a distinct, separately invoked
validator (see ``app.main``'s lifespan, never inserted into the fixed
``StartupValidationRunner.default_validators()`` list) so a future need to
enforce this metadata (e.g. before a mission's agents are provisioned into
a real Foundry project) has a ready home.
"""
from __future__ import annotations

from app.config.settings import Settings
from app.validation.base import ValidationResult

__all__ = ["FoundryAgentRegistryValidator"]


class FoundryAgentRegistryValidator:
    """Currently a no-op; see module docstring."""

    name = "FoundryAgentRegistryValidator"

    def validate(self, settings: Settings) -> ValidationResult:
        del settings
        return ValidationResult.ok(self.name)
