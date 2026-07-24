"""Validates core configuration values are present and well-formed."""
from __future__ import annotations

from app.config.settings import Settings
from app.validation.base import ValidationResult

_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class ConfigurationValidator:
    """Fails closed if required base configuration is missing or malformed."""

    name = "ConfigurationValidator"

    def validate(self, settings: Settings) -> ValidationResult:
        errors: list[str] = []

        if not settings.service_name.strip():
            errors.append("service_name must not be empty.")

        if settings.log_level.upper() not in _VALID_LOG_LEVELS:
            errors.append(
                f"log_level '{settings.log_level}' must be one of {sorted(_VALID_LOG_LEVELS)}."
            )

        if not settings.config_root:
            errors.append("config_root must be set.")
        elif not settings.config_root.is_dir():
            errors.append(f"config_root '{settings.config_root}' does not exist.")

        if not settings.default_llm.strip():
            errors.append("default_llm must not be empty.")

        if errors:
            return ValidationResult.fail(self.name, errors)
        return ValidationResult.ok(self.name)
