"""Unit tests for AgentRegistry loading and validation."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.registry import AgentRegistry, AgentRegistryError


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_loads_valid_multi_agent_file(tmp_path: Path):
    _write(
        tmp_path / "registry.yaml",
        """
agents:
  - id: agent-a
    name: Agent A
    role: role_a
    description: First agent.
    model_deployment_ref: model-a
  - id: agent-b
    name: Agent B
    role: role_b
    description: Second agent.
    model_deployment_ref: model-b
""",
    )

    registry = AgentRegistry.load(tmp_path)

    assert len(registry) == 2
    assert "agent-a" in registry
    assert registry.get("agent-b").name == "Agent B"
    assert {agent.id for agent in registry.list()} == {"agent-a", "agent-b"}


def test_fails_closed_when_directory_missing(tmp_path: Path):
    with pytest.raises(AgentRegistryError, match="No agent registry files found"):
        AgentRegistry.load(tmp_path / "missing")


def test_fails_closed_on_duplicate_id_across_files(tmp_path: Path):
    _write(
        tmp_path / "a.yaml",
        "agents:\n  - id: dup\n    name: A\n    role: r\n    description: d\n"
        "    model_deployment_ref: m\n",
    )
    _write(
        tmp_path / "b.yaml",
        "agents:\n  - id: dup\n    name: B\n    role: r\n    description: d\n"
        "    model_deployment_ref: m\n",
    )

    with pytest.raises(AgentRegistryError, match="Duplicate agent id"):
        AgentRegistry.load(tmp_path)


def test_fails_closed_on_invalid_yaml(tmp_path: Path):
    _write(tmp_path / "registry.yaml", "agents: [this is not: valid yaml")

    with pytest.raises(AgentRegistryError):
        AgentRegistry.load(tmp_path)


def test_fails_closed_on_missing_required_field(tmp_path: Path):
    _write(tmp_path / "registry.yaml", "agents:\n  - id: incomplete\n    name: X\n")

    with pytest.raises(AgentRegistryError, match="Invalid agent definition"):
        AgentRegistry.load(tmp_path)


def test_fails_closed_when_agents_key_missing(tmp_path: Path):
    _write(tmp_path / "registry.yaml", "not_agents: []\n")

    with pytest.raises(AgentRegistryError, match="top-level 'agents' list"):
        AgentRegistry.load(tmp_path)


def test_fails_closed_when_registry_empty(tmp_path: Path):
    _write(tmp_path / "registry.yaml", "agents: []\n")

    with pytest.raises(AgentRegistryError, match="is empty"):
        AgentRegistry.load(tmp_path)


def test_agent_without_model_deployment_ref_falls_back_to_default_llm(tmp_path: Path):
    _write(
        tmp_path / "registry.yaml",
        "agents:\n  - id: agent-a\n    name: Agent A\n    role: role_a\n"
        "    description: First agent.\n",
    )

    registry = AgentRegistry.load(tmp_path, default_llm="claude-sonnet-5")

    assert registry.get("agent-a").model_deployment_ref == "claude-sonnet-5"


def test_agent_with_explicit_model_deployment_ref_overrides_default_llm(tmp_path: Path):
    _write(
        tmp_path / "registry.yaml",
        "agents:\n  - id: agent-a\n    name: Agent A\n    role: role_a\n"
        "    description: First agent.\n    model_deployment_ref: gpt-4o\n",
    )

    registry = AgentRegistry.load(tmp_path, default_llm="claude-sonnet-5")

    assert registry.get("agent-a").model_deployment_ref == "gpt-4o"


def test_fails_closed_when_default_llm_blank(tmp_path: Path):
    _write(
        tmp_path / "registry.yaml",
        "agents:\n  - id: agent-a\n    name: Agent A\n    role: role_a\n"
        "    description: First agent.\n",
    )

    with pytest.raises(AgentRegistryError, match="default_llm must not be blank"):
        AgentRegistry.load(tmp_path, default_llm="   ")
