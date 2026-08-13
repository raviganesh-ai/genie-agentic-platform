"""Fail-closed tests for ProductionSafetyValidator.

These tests guard the "Production mode must never..." rules in
``.github/copilot-instructions.md``: mock/local agents, synthetic data, and
missing Azure Foundry / Key Vault configuration must all fail closed.
"""
from __future__ import annotations

from app.validation.production_safety_validator import ProductionSafetyValidator


def test_local_mode_always_passes(local_settings):
    assert ProductionSafetyValidator().validate(local_settings).passed


def test_production_passes_with_safe_config(production_settings):
    assert ProductionSafetyValidator().validate(production_settings).passed


def test_production_fails_closed_when_mock_agents_allowed(production_settings):
    broken = production_settings.model_copy(update={"allow_mock_agents": True})
    result = ProductionSafetyValidator().validate(broken)
    assert not result.passed


def test_production_fails_closed_when_local_agents_allowed(production_settings):
    broken = production_settings.model_copy(update={"allow_local_agents": True})
    result = ProductionSafetyValidator().validate(broken)
    assert not result.passed


def test_production_fails_closed_when_local_token_validation_allowed(production_settings):
    broken = production_settings.model_copy(update={"allow_local_token_validation": True})
    result = ProductionSafetyValidator().validate(broken)
    assert not result.passed


def test_production_fails_closed_when_synthetic_data_enabled(production_settings):
    broken = production_settings.model_copy(update={"use_synthetic_data": True})
    result = ProductionSafetyValidator().validate(broken)
    assert not result.passed


def test_production_fails_closed_when_foundry_endpoint_missing(production_settings):
    broken = production_settings.model_copy(update={"azure_foundry_endpoint": None})
    result = ProductionSafetyValidator().validate(broken)
    assert not result.passed


def test_production_fails_closed_when_key_vault_uri_missing(production_settings):
    broken = production_settings.model_copy(update={"key_vault_uri": None})
    result = ProductionSafetyValidator().validate(broken)
    assert not result.passed


def test_production_fails_closed_when_mise_endpoint_missing(production_settings):
    broken = production_settings.model_copy(update={"mise_endpoint": None})
    result = ProductionSafetyValidator().validate(broken)
    assert not result.passed
