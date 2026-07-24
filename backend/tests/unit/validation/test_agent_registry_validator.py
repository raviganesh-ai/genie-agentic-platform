"""Fail-closed tests for AgentRegistryValidator."""
from __future__ import annotations

import shutil

from app.validation.agent_registry_validator import AgentRegistryValidator


def test_passes_when_registry_populated(local_settings):
    assert AgentRegistryValidator().validate(local_settings).passed


def test_fails_closed_when_directory_missing(local_settings):
    shutil.rmtree(local_settings.agents_path)
    result = AgentRegistryValidator().validate(local_settings)
    assert not result.passed


def test_fails_closed_when_directory_empty(local_settings):
    for item in local_settings.agents_path.iterdir():
        item.unlink()
    result = AgentRegistryValidator().validate(local_settings)
    assert not result.passed
