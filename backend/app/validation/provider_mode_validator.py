"""Validates the configured provider mode is one of the supported values."""
from __future__ import annotations

from app.config.settings import Settings
from app.validation.base import ValidationResult

_VALID_MODES = {"local", "production"}


class ProviderModeValidator:
    """Fails closed if provider_mode is not an explicitly supported value."""

    name = "ProviderModeValidator"

    def validate(self, settings: Settings) -> ValidationResult:
        if settings.provider_mode not in _VALID_MODES:
            return ValidationResult.fail(
                self.name,
                [
                    (
                        f"provider_mode '{settings.provider_mode}' must be one of "
                        f"{sorted(_VALID_MODES)}."
                    )
                ],
            )
        return ValidationResult.ok(self.name)
