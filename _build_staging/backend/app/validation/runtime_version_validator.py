"""Validates the running Python interpreter meets the minimum supported version."""
from __future__ import annotations

import sys

from app.config.settings import Settings
from app.validation.base import ValidationResult

REQUIRED_MAJOR = 3
REQUIRED_MINOR = 12


class RuntimeVersionValidator:
    """Fails closed if the interpreter is older than Python 3.12."""

    name = "RuntimeVersionValidator"

    def __init__(self, python_version: tuple[int, int] | None = None) -> None:
        self._python_version = python_version or (
            sys.version_info.major,
            sys.version_info.minor,
        )

    def validate(self, settings: Settings) -> ValidationResult:
        if self._python_version < (REQUIRED_MAJOR, REQUIRED_MINOR):
            major, minor = self._python_version
            return ValidationResult.fail(
                self.name,
                [
                    (
                        f"Python {REQUIRED_MAJOR}.{REQUIRED_MINOR}+ is required, "
                        f"found {major}.{minor}."
                    )
                ],
            )
        return ValidationResult.ok(self.name)
