"""Fail-closed tests for GovernanceProviderValidator."""
from __future__ import annotations

from app.validation.governance_provider_validator import GovernanceProviderValidator


def test_local_provider_passes_in_local_mode(local_settings):
    assert GovernanceProviderValidator().validate(local_settings).passed


def test_agent365_provider_passes_for_foundry_configured_settings(foundry_configured_settings):
    assert GovernanceProviderValidator().validate(foundry_configured_settings).passed


def test_fails_closed_for_unknown_provider(local_settings):
    broken = local_settings.model_copy(update={"governance_provider": "unknown"})
    result = GovernanceProviderValidator().validate(broken)
    assert not result.passed
