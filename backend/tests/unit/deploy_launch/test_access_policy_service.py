"""Unit tests for AccessPolicyService."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.registry import AgentRegistry
from app.deploy_launch.access_policy_service import AccessPolicyService


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def agent_registry(tmp_path: Path) -> AgentRegistry:
    _write(
        tmp_path / "registry.yaml",
        """
agents:
  - id: requirements-analyst
    name: Requirements Analyst
    role: requirements_analysis
    description: Extracts requirements.
    allowed_tools: [azure_ai_search]
    memory_access: [personal, shared]
    model_deployment_ref: model-a
  - id: disabled-agent
    name: Disabled Agent
    role: unused
    description: Should not appear in the policy.
    enabled: false
    model_deployment_ref: model-a
""",
    )
    return AgentRegistry.load(tmp_path, default_llm="model-a")


def test_generate_builds_policy_from_enabled_agents_only(agent_registry: AgentRegistry):
    service = AccessPolicyService(agent_registry=agent_registry)

    document = service.generate()

    assert len(document.agents) == 1
    grant = document.agents[0]
    assert grant.agent_id == "requirements-analyst"
    assert grant.role == "requirements_analysis"
    assert grant.allowed_tools == ["azure_ai_search"]
    assert grant.memory_access == ["personal", "shared"]


def test_generate_excludes_disabled_agents(agent_registry: AgentRegistry):
    service = AccessPolicyService(agent_registry=agent_registry)

    document = service.generate()

    assert all(grant.agent_id != "disabled-agent" for grant in document.agents)
