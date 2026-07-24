"""Fail-closed tests for ConfigurationValidator."""
from __future__ import annotations

from app.validation.configuration_validator import ConfigurationValidator


def test_passes_for_valid_settings(local_settings):
    result = ConfigurationValidator().validate(local_settings)
    assert result.passed


def test_fails_closed_when_config_root_missing(local_settings, tmp_path):
    missing = tmp_path / "does-not-exist"
    broken = local_settings.model_copy(update={"config_root": missing})
    result = ConfigurationValidator().validate(broken)
    assert not result.passed
    assert any("does not exist" in issue.message for issue in result.issues)


def test_fails_closed_for_invalid_log_level(local_settings):
    broken = local_settings.model_copy(update={"log_level": "VERBOSE"})
    result = ConfigurationValidator().validate(broken)
    assert not result.passed


def test_fails_closed_for_empty_service_name(local_settings):
    broken = local_settings.model_copy(update={"service_name": "   "})
    result = ConfigurationValidator().validate(broken)
    assert not result.passed


def test_fails_closed_for_blank_default_llm(local_settings):
    broken = local_settings.model_copy(update={"default_llm": "   "})
    result = ConfigurationValidator().validate(broken)
    assert not result.passed
