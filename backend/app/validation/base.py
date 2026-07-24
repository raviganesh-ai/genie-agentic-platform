"""Shared types used by every fail-closed startup validator."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.config.settings import Settings


@dataclass(frozen=True)
class ValidationIssue:
    """A single problem discovered by a validator."""

    validator: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of running a single validator."""

    validator: str
    passed: bool
    issues: tuple[ValidationIssue, ...] = field(default_factory=tuple)

    @classmethod
    def ok(cls, validator: str) -> ValidationResult:
        return cls(validator=validator, passed=True, issues=())

    @classmethod
    def fail(cls, validator: str, messages: list[str]) -> ValidationResult:
        return cls(
            validator=validator,
            passed=False,
            issues=tuple(ValidationIssue(validator=validator, message=m) for m in messages),
        )


class StartupValidator(Protocol):
    """Protocol implemented by every fail-closed startup validator."""

    name: str

    def validate(self, settings: Settings) -> ValidationResult:
        ...


class StartupValidationError(RuntimeError):
    """Raised when one or more startup validators fail.

    The application must not accept traffic when this is raised; callers
    must not implement fallback behavior in response to it.
    """

    def __init__(self, results: list[ValidationResult]) -> None:
        self.results = [result for result in results if not result.passed]
        messages = [
            f"[{issue.validator}] {issue.message}"
            for result in self.results
            for issue in result.issues
        ]
        super().__init__("Startup validation failed:\n" + "\n".join(messages))
