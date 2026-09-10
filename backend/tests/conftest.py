"""Shared pytest fixtures for validation and startup tests.

These fixtures build hermetic, temporary configuration trees so validator
and application tests never depend on (or mutate) the real ``config/``
directory used by local development or production deployments.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.config.settings import Settings


def _write_valid_config_tree(root: Path) -> None:
    agents_dir = root / "agents"
    prompts_dir = root / "prompts"
    workflows_dir = root / "workflows"
    policies_dir = root / "policies"

    for directory in (agents_dir, prompts_dir, workflows_dir, policies_dir):
        directory.mkdir(parents=True, exist_ok=True)

    (agents_dir / "registry.yaml").write_text(
        "agents:\n"
        "  - id: test-agent\n"
        "    name: Test Agent\n"
        "    role: test_role\n"
        "    description: A minimal valid agent used for testing.\n"
        "    model_deployment_ref: test-model\n",
        encoding="utf-8",
    )
    (prompts_dir / "registry.yaml").write_text(
        "prompts:\n"
        "  - id: test-prompt\n"
        "    name: Test Prompt\n"
        "    description: A minimal valid prompt used for testing.\n"
        "    template: 'Hello {name}'\n"
        "    variables:\n"
        "      - name\n",
        encoding="utf-8",
    )
    (workflows_dir / "registry.yaml").write_text(
        "workflows:\n"
        "  - id: test-workflow\n"
        "    name: Test Workflow\n"
        "    description: A minimal valid workflow used for testing.\n"
        "    steps:\n"
        "      - id: step-one\n"
        "        agent_id: test-agent\n"
        "        description: The only step.\n",
        encoding="utf-8",
    )
    (policies_dir / "memory_policy.yaml").write_text(
        "personal_agent_memory:\n"
        "  accessible_by: owning_agent\n"
        "shared_collaboration_memory:\n"
        "  accessible_by: session_participants\n"
        "  emits_governance_events: true\n"
        "  require_approval_for_overwrite: true\n"
        "enterprise_knowledge_memory:\n"
        "  accessible_by: approved_reviewers\n"
        "  requires_approval_to_promote: true\n",
        encoding="utf-8",
    )
    (policies_dir / "governance_policy.yaml").write_text(
        "agent_execution_governance:\n"
        "  track_registration: true\n"
        "  track_versions: true\n"
        "  track_lifecycle: true\n"
        "  track_executions: true\n"
        "  track_communication: true\n"
        "  track_memory_reads: true\n"
        "  track_memory_writes: true\n"
        "  track_tool_requests: true\n"
        "  track_policy_evaluations: true\n"
        "  track_denied_access: true\n"
        "decision_lineage:\n"
        "  require_evidence_references: true\n"
        "session_replay:\n"
        "  enabled: true\n",
        encoding="utf-8",
    )
    (policies_dir / "approval_policy.yaml").write_text(
        "default_expiry_minutes: 1440\n"
        "checkpoints:\n"
        "  - id: test-checkpoint\n"
        "    name: Test Checkpoint\n"
        "    description: A minimal valid approval checkpoint used for testing.\n"
        "    required: true\n",
        encoding="utf-8",
    )


@pytest.fixture
def valid_config_root(tmp_path: Path) -> Path:
    """A temporary, fully populated, secret-free configuration tree."""

    config_root = tmp_path / "config"
    _write_valid_config_tree(config_root)
    return config_root


@pytest.fixture
def local_settings(valid_config_root: Path) -> Settings:
    """Settings representing a safe local development configuration."""

    return Settings(
        environment="development",
        governance_provider="local",
        allow_mock_agents=True,
        allow_local_agents=True,
        allow_local_token_validation=True,
        use_synthetic_data=True,
        config_root=valid_config_root,
    )


@pytest.fixture
def app_local_settings(local_settings: Settings) -> Settings:
    """Local agent/auth settings with real deployment-provider construction."""

    return local_settings.model_copy(
        update={
            "azure_subscription_id": "test-subscription",
            "azure_foundry_endpoint": "https://example.invalid/foundry",
            "azure_foundry_project_name": "test-project",
            "deployment_resource_group": "test-resource-group",
            "deployment_acr_name": "testacr",
            "deployment_container_apps_environment_id": "/test/container-apps-environment",
            "deployment_location": "eastus2",
        }
    )


@pytest.fixture
def foundry_configured_settings(valid_config_root: Path) -> Settings:
    """Settings representing a fully configured real-Azure deployment.

    Genie is a personal dev/demo deployment with no separate production
    tier - this fixture exists for tests that need every optional Azure
    integration (Foundry, Key Vault, Cosmos DB-backed memory/lineage
    stores) configured, as opposed to ``local_settings``'s local/mock
    defaults.
    """

    return Settings(
        environment="production",
        governance_provider="agent365",
        allow_mock_agents=False,
        allow_local_agents=False,
        allow_local_token_validation=False,
        use_synthetic_data=False,
        azure_foundry_endpoint="https://genie-foundry.example-project.azure.com",
        azure_foundry_project_name="genie-prod-project",
        key_vault_uri="https://genie-kv.vault.azure.net/",
        memory_store_backend="cosmos_db",
        memory_store_endpoint="https://genie-memory.example-project.documents.azure.com/",
        lineage_store_backend="cosmos_db",
        lineage_store_endpoint="https://genie-lineage.example-project.documents.azure.com/",
        azure_subscription_id="00000000-0000-0000-0000-000000000000",
        deployment_resource_group="genie-example-rg",
        deployment_acr_name="genieexampleacr",
        deployment_container_apps_environment_id=(
            "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/"
            "genie-example-rg/providers/Microsoft.App/managedEnvironments/genie-example-cae"
        ),
        deployment_storage_account_name="genieexamplestorage",
        deployment_location="eastus2",
        prototype_api_gateway_enabled=True,
        prototype_api_gateway_publisher_email="genie@example.com",
        prototype_api_gateway_publisher_name="Genie",
        prototype_test_principal_client_id="00000000-0000-0000-0000-000000000001",
        entra_tenant_id="00000000-0000-0000-0000-000000000002",
        config_root=valid_config_root,
    )
