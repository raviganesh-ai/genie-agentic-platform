"""Fail-closed tests for RuntimeVersionValidator."""
from __future__ import annotations

from app.validation.runtime_version_validator import RuntimeVersionValidator


def test_passes_on_supported_version(local_settings):
    validator = RuntimeVersionValidator(python_version=(3, 12))
    result = validator.validate(local_settings)
    assert result.passed


def test_passes_on_newer_version(local_settings):
    validator = RuntimeVersionValidator(python_version=(3, 13))
    result = validator.validate(local_settings)
    assert result.passed


def test_fails_closed_on_unsupported_version(local_settings):
    validator = RuntimeVersionValidator(python_version=(3, 11))
    result = validator.validate(local_settings)
    assert not result.passed
    assert "3.12" in result.issues[0].message
