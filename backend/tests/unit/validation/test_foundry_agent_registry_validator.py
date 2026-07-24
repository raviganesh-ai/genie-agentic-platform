"""Unit tests for FoundryAgentRegistryValidator."""
from __future__ import annotations

from pathlib import Path

from app.config.settings import Settings
from app.validation.foundry_agent_registry_validator import FoundryAgentRegistryValidator


def _write_agents(directory: Path, agents_yaml: str) -> None:
    (directory / "agents").mkdir(parents=True, exist_ok=True)
    (directory / "agents" / "registry.yaml").write_text(agents_yaml, encoding="utf-8")


_COMPLETE_AGENT = """
agents:
  - id: agent-a
    name: Agent A
    role: test_role
    description: A test agent.
    foundry_agent_id: agent-a-foundry
    owner: test-team
    governance_policy_id: standard-governance-policy-v1
    prompt_template_ref: test-prompt
    memory_access: [shared]
    enabled: true
"""

_INCOMPLETE_AGENT = """
agents:
  - id: agent-a
    name: Agent A
    role: test_role
    description: A test agent.
    enabled: true
"""


def test_no_op_outside_production(local_settings: Settings):
    result = FoundryAgentRegistryValidator().validate(local_settings)

    assert result.passed


def test_passes_in_production_for_complete_metadata(production_settings: Settings):
    _write_agents(production_settings.config_root, _COMPLETE_AGENT)

    result = FoundryAgentRegistryValidator().validate(production_settings)

    assert result.passed, result.issues


def test_fails_in_production_for_incomplete_metadata(production_settings: Settings):
    _write_agents(production_settings.config_root, _INCOMPLETE_AGENT)

    result = FoundryAgentRegistryValidator().validate(production_settings)

    assert not result.passed
    messages = [issue.message for issue in result.issues]
    assert any("foundryAgentReference" in message for message in messages)
    assert any("governancePolicyId" in message for message in messages)
    assert any("promptTemplateRef" in message for message in messages)
    assert any("owner" in message for message in messages)


def test_disabled_agents_are_not_checked(production_settings: Settings):
    disabled_agent = _INCOMPLETE_AGENT.replace("enabled: true", "enabled: false")
    _write_agents(production_settings.config_root, disabled_agent)

    result = FoundryAgentRegistryValidator().validate(production_settings)

    assert result.passed
