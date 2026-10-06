"""Fail-closed tests for ProductionSafetyValidator.

Rewritten alongside the first-party authentication addition - see
``app.security.auth_service``. Covers the pre-existing production-only
checks plus the new ``auth_enabled``/secret-reference requirements.
"""
from __future__ import annotations

from app.validation.production_safety_validator import ProductionSafetyValidator


def test_passes_in_development_regardless_of_production_only_fields(local_settings):
    result = ProductionSafetyValidator().validate(local_settings)
    assert result.passed


def test_passes_for_fully_configured_production_settings(foundry_configured_settings):
    result = ProductionSafetyValidator().validate(foundry_configured_settings)
    assert result.passed


def test_fails_closed_when_governance_provider_is_not_agent365(foundry_configured_settings):
    broken = foundry_configured_settings.model_copy(update={"governance_provider": "local"})
    result = ProductionSafetyValidator().validate(broken)
    assert not result.passed


def test_fails_closed_when_memory_store_backend_is_not_cosmos_db(foundry_configured_settings):
    broken = foundry_configured_settings.model_copy(update={"memory_store_backend": "in_memory"})
    result = ProductionSafetyValidator().validate(broken)
    assert not result.passed


def test_fails_closed_when_key_vault_uri_missing(foundry_configured_settings):
    broken = foundry_configured_settings.model_copy(update={"key_vault_uri": None})
    result = ProductionSafetyValidator().validate(broken)
    assert not result.passed


def test_fails_closed_when_auth_is_not_enabled(foundry_configured_settings):
    broken = foundry_configured_settings.model_copy(update={"auth_enabled": False})
    result = ProductionSafetyValidator().validate(broken)
    assert not result.passed
    assert any("auth_enabled must be true" in issue.message for issue in result.issues)


def test_fails_closed_when_auth_enabled_without_signing_key_env_var(foundry_configured_settings):
    broken = foundry_configured_settings.model_copy(
        update={"auth_token_signing_key_env_var": None}
    )
    result = ProductionSafetyValidator().validate(broken)
    assert not result.passed
    assert any("auth_token_signing_key_env_var is required" in issue.message for issue in result.issues)


def test_fails_closed_when_auth_enabled_without_users_env_var(foundry_configured_settings):
    broken = foundry_configured_settings.model_copy(update={"auth_users_env_var": None})
    result = ProductionSafetyValidator().validate(broken)
    assert not result.passed
    assert any("auth_users_env_var is required" in issue.message for issue in result.issues)
