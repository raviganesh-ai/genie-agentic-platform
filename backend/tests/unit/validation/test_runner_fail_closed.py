"""End-to-end fail-closed behavior of StartupValidationRunner."""
from __future__ import annotations

import pytest

from app.validation.base import StartupValidationError
from app.validation.runner import StartupValidationRunner


def test_runner_passes_for_valid_local_settings(local_settings):
    runner = StartupValidationRunner()
    results = runner.run_or_raise(local_settings)
    assert all(result.passed for result in results)


def test_runner_passes_for_foundry_configured_settings(foundry_configured_settings):
    runner = StartupValidationRunner()
    results = runner.run_or_raise(foundry_configured_settings)
    assert all(result.passed for result in results)


def test_runner_fails_closed_when_config_missing(local_settings, tmp_path):
    missing = tmp_path / "no-config-here"
    broken = local_settings.model_copy(update={"config_root": missing})
    runner = StartupValidationRunner()
    with pytest.raises(StartupValidationError):
        runner.run_or_raise(broken)


def test_runner_reports_every_failing_validator(local_settings):
    unsafe = local_settings.model_copy(update={"governance_provider": "unknown"})
    (unsafe.policies_path / "memory_policy.yaml").unlink()
    runner = StartupValidationRunner()
    with pytest.raises(StartupValidationError) as exc_info:
        runner.run_or_raise(unsafe)
    message = str(exc_info.value)
    assert "GovernanceProviderValidator" in message
    assert "MemoryPolicyValidator" in message
