"""Unit tests for FoundryAgentRegistryValidator.

This validator is currently a permanent no-op (see its module docstring):
Genie is a personal dev/demo deployment with no separate production tier
that requires enabled agents to carry complete Foundry provisioning
metadata, so there is nothing else to exercise here beyond confirming it
always passes.
"""
from __future__ import annotations

from app.config.settings import Settings
from app.validation.foundry_agent_registry_validator import FoundryAgentRegistryValidator


def test_always_passes(local_settings: Settings):
    result = FoundryAgentRegistryValidator().validate(local_settings)

    assert result.passed


def test_always_passes_for_foundry_configured_settings(foundry_configured_settings: Settings):
    result = FoundryAgentRegistryValidator().validate(foundry_configured_settings)

    assert result.passed
