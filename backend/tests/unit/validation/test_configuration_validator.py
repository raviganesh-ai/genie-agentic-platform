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


def test_passes_for_production_settings_with_deployment_config(foundry_configured_settings):
    result = ConfigurationValidator().validate(foundry_configured_settings)
    assert result.passed


def test_fails_closed_in_production_when_deployment_resource_group_missing(
    foundry_configured_settings,
):
    broken = foundry_configured_settings.model_copy(update={"deployment_resource_group": None})
    result = ConfigurationValidator().validate(broken)
    assert not result.passed


def test_fails_closed_in_production_when_deployment_acr_name_missing(foundry_configured_settings):
    broken = foundry_configured_settings.model_copy(update={"deployment_acr_name": None})
    result = ConfigurationValidator().validate(broken)
    assert not result.passed


def test_fails_closed_in_production_when_deployment_container_apps_environment_id_missing(
    foundry_configured_settings,
):
    broken = foundry_configured_settings.model_copy(
        update={"deployment_container_apps_environment_id": None}
    )
    result = ConfigurationValidator().validate(broken)
    assert not result.passed


def test_fails_closed_in_production_when_deployment_storage_account_name_missing(
    foundry_configured_settings,
):
    broken = foundry_configured_settings.model_copy(update={"deployment_storage_account_name": None})
    result = ConfigurationValidator().validate(broken)
    assert not result.passed


def test_fails_closed_in_production_when_deployment_location_missing(foundry_configured_settings):
    broken = foundry_configured_settings.model_copy(update={"deployment_location": None})
    result = ConfigurationValidator().validate(broken)
    assert not result.passed


def test_missing_deployment_config_does_not_fail_in_development(local_settings):
    assert local_settings.deployment_resource_group is None
    result = ConfigurationValidator().validate(local_settings)
    assert result.passed
