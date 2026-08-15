"""Validates the memory tier access policy configuration.

Fully parses and schema-validates ``config/policies/memory_policy.yaml``
via ``MemoryAccessPolicyService`` (Phase 4 memory services) - not just
file existence - so a missing or malformed shared/personal/enterprise
policy section fails startup closed.
"""
from __future__ import annotations

from app.config.settings import Settings
from app.memory.memory_access_policy_service import MemoryAccessPolicyService, MemoryPolicyError
from app.validation.base import ValidationResult

_REQUIRED_FILE = "memory_policy.yaml"


class MemoryPolicyValidator:
    """Fails closed if the memory tier access policy is missing or invalid."""

    name = "MemoryPolicyValidator"

    def validate(self, settings: Settings) -> ValidationResult:
        errors: list[str] = []

        path = settings.policies_path / _REQUIRED_FILE
        if not path.is_file():
            errors.append(f"Memory policy file '{path}' does not exist.")
        else:
            try:
                MemoryAccessPolicyService.load(settings.policies_path)
            except MemoryPolicyError as exc:
                errors.append(str(exc))

        if errors:
            return ValidationResult.fail(self.name, errors)
        return ValidationResult.ok(self.name)
