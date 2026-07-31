"""Integration tests exercising Phase 10A validators against the real repo config.

Loads the actual ``config/agents``, ``config/prompts``, and
``config/workflows`` directories (not a hermetic ``tmp_path`` fixture) to
confirm the real Foundry Supported Agents catalog satisfies
``FoundryAgentRegistryValidator``/``FoundryAgentDriftValidator`` under
production-shaped settings, without constructing the full application
(governance provider resolution, which requires a real Agent365 SDK
integration in production, is intentionally out of scope here - see
``tests/integration/test_app_startup.py``).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.registry import AgentRegistry
from app.config.settings import Settings
from app.prompts.registry import PromptRegistry
from app.validation.foundry_agent_drift_validator import FoundryAgentDriftValidator
from app.validation.foundry_agent_registry_validator import FoundryAgentRegistryValidator
from app.workflows.registry import WorkflowRegistry

_REPO_CONFIG_ROOT = Path(__file__).resolve().parents[3] / "config"


@pytest.fixture
def real_config_production_settings() -> Settings:
    return Settings(
        environment="production",
        provider_mode="production",
        governance_provider="agent365",
        allow_mock_agents=False,
        allow_local_agents=False,
        use_synthetic_data=False,
        azure_foundry_endpoint="https://genie-foundry.example-project.azure.com",
        azure_foundry_project_name="genie-prod-project",
        key_vault_uri="https://genie-kv.vault.azure.net/",
        memory_store_backend="cosmos_db",
        memory_store_endpoint="https://genie-memory.example-project.documents.azure.com/",
        lineage_store_backend="cosmos_db",
        lineage_store_endpoint="https://genie-lineage.example-project.documents.azure.com/",
        entra_tenant_id="00000000-0000-0000-0000-000000000000",
        entra_client_id="11111111-1111-1111-1111-111111111111",
        config_root=_REPO_CONFIG_ROOT,
    )


def test_real_config_agent_registry_loads_every_catalog_agent(
    real_config_production_settings: Settings,
):
    registry = AgentRegistry.load(
        real_config_production_settings.agents_path,
        default_llm=real_config_production_settings.default_llm,
    )

    # requirements-analyst, architecture-designer, peer-review-agent,
    # debugging-agent, build-agent, genie-orchestrator,
    # security-assessment-agent, test-generation-agent.
    assert len(registry) == 8
    assert "requirements-analyst" in registry
    assert "genie-orchestrator" in registry
    assert "build-agent" in registry
    assert "peer-review-agent" in registry


def test_real_config_prompt_registry_resolves_every_agent_prompt_ref(
    real_config_production_settings: Settings,
):
    agent_registry = AgentRegistry.load(
        real_config_production_settings.agents_path,
        default_llm=real_config_production_settings.default_llm,
    )
    prompt_registry = PromptRegistry.load(real_config_production_settings.prompts_path)

    for agent in agent_registry.list():
        assert agent.prompt_template_ref is not None
        assert agent.prompt_template_ref in prompt_registry


def test_real_config_passes_foundry_agent_registry_validator_for_enabled_agents(
    real_config_production_settings: Settings,
):
    result = FoundryAgentRegistryValidator().validate(real_config_production_settings)

    assert result.passed, result.issues


def test_real_config_passes_foundry_agent_drift_validator(
    real_config_production_settings: Settings,
):
    result = FoundryAgentDriftValidator().validate(real_config_production_settings)

    assert result.passed, result.issues


def test_real_config_workflow_registry_agent_references_are_valid(
    real_config_production_settings: Settings,
):
    agent_registry = AgentRegistry.load(
        real_config_production_settings.agents_path,
        default_llm=real_config_production_settings.default_llm,
    )
    workflow_registry = WorkflowRegistry.load(real_config_production_settings.workflows_path)

    assert workflow_registry.validate_agent_references(agent_registry) == []
