"""Fail-closed tests for ProviderModeValidator."""
from __future__ import annotations

from app.validation.provider_mode_validator import ProviderModeValidator


def test_passes_for_local_mode(local_settings):
    assert ProviderModeValidator().validate(local_settings).passed


def test_passes_for_production_mode(production_settings):
    assert ProviderModeValidator().validate(production_settings).passed


def test_fails_closed_for_unknown_mode(local_settings):
    broken = local_settings.model_copy(update={"provider_mode": "staging"})
    result = ProviderModeValidator().validate(broken)
    assert not result.passed
