"""Unit tests for AccessPolicyService."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.agents.registry import AgentRegistry
from app.deploy_launch.access_policy_service import AccessPolicyService
from app.deploy_launch.mission_identity_service import (
    MissionIdentityService,
    NullMissionIdentityService,
)


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


@pytest.mark.asyncio
async def test_generate_builds_policy_from_enabled_agents_only(agent_registry: AgentRegistry):
    service = AccessPolicyService(
        agent_registry=agent_registry,
        mission_identity_service=NullMissionIdentityService(),
    )

    document = await service.generate(mission_id="test-mission")

    assert len(document.agents) == 1
    grant = document.agents[0]
    assert grant.agent_id == "requirements-analyst"
    assert grant.role == "requirements_analysis"
    assert grant.allowed_tools == ["azure_ai_search"]
    assert grant.memory_access == ["personal", "shared"]


@pytest.mark.asyncio
async def test_generate_excludes_disabled_agents(agent_registry: AgentRegistry):
    service = AccessPolicyService(
        agent_registry=agent_registry,
        mission_identity_service=NullMissionIdentityService(),
    )

    document = await service.generate(mission_id="test-mission")

    assert all(grant.agent_id != "disabled-agent" for grant in document.agents)


@pytest.mark.asyncio
async def test_generate_passes_exact_acr_scope_to_identity_service(agent_registry: AgentRegistry):
    recorded: dict[str, str | None] = {}

    class RecordingIdentityService(NullMissionIdentityService):
        async def provision(self, *, mission_id: str, acr_id: str | None = None, **kwargs):
            recorded["acr_id"] = acr_id
            return await super().provision(mission_id=mission_id, acr_id=acr_id, **kwargs)

    acr_id = (
        "/subscriptions/sub-1/resourceGroups/rg-1/providers/"
        "Microsoft.ContainerRegistry/registries/acr1"
    )
    service = AccessPolicyService(
        agent_registry=agent_registry,
        mission_identity_service=RecordingIdentityService(),
        acr_id=acr_id,
    )

    await service.generate(mission_id="test-mission")

    assert recorded["acr_id"] == acr_id


@pytest.mark.asyncio
async def test_mission_identity_uses_configured_location_and_guid_assignment(
    monkeypatch, local_settings
):
    import app.deploy_launch.mission_identity_service as identity_module

    captured: dict[str, object] = {}
    identity = SimpleNamespace(
        id="/subscriptions/sub-1/resourceGroups/rg-1/providers/Microsoft.ManagedIdentity/userAssignedIdentities/mission",
        principal_id="principal-1",
        client_id="client-1",
    )
    msi_client = SimpleNamespace(
        user_assigned_identities=SimpleNamespace(
            create_or_update=lambda **kwargs: captured.update(
                {"location": kwargs["parameters"].location}
            )
            or identity,
            delete=lambda **kwargs: None,
        )
    )

    def create_assignment(**kwargs):
        captured["assignment_name"] = kwargs["role_assignment_name"]
        captured["scope"] = kwargs["scope"]
        return SimpleNamespace(id="assignment-1")

    authz_client = SimpleNamespace(
        role_assignments=SimpleNamespace(create=create_assignment)
    )
    monkeypatch.setattr(
        identity_module, "ManagedServiceIdentityClient", lambda *_: msi_client
    )
    monkeypatch.setattr(
        identity_module, "AuthorizationManagementClient", lambda *_: authz_client
    )
    settings = local_settings.model_copy(update={"deployment_location": "westus3"})
    service = MissionIdentityService(
        settings=settings, subscription_id="sub-1", resource_group_name="rg-1"
    )
    service._credential = object()
    acr_id = (
        "/subscriptions/sub-1/resourceGroups/rg-1/providers/"
        "Microsoft.ContainerRegistry/registries/acr1"
    )

    result = await service.provision(mission_id="mission-1", acr_id=acr_id)

    assert captured["location"] == "westus3"
    assert captured["scope"] == acr_id
    UUID(str(captured["assignment_name"]))
    assert result.role_assignments[0].role_name == "acr_pull"


@pytest.mark.asyncio
async def test_mission_identity_delete_removes_all_principal_roles_before_identity(
    monkeypatch, local_settings
):
    import app.deploy_launch.mission_identity_service as identity_module

    events: list[str] = []
    role_assignments = SimpleNamespace(
        list_for_subscription=lambda **kwargs: [
            SimpleNamespace(id="assignment-acr"),
            SimpleNamespace(id="assignment-foundry"),
        ],
        delete_by_id=lambda assignment_id: events.append(f"role:{assignment_id}"),
    )
    identity_delete_poller = SimpleNamespace(
        result=lambda: events.append("identity:mission-identity")
    )
    identity_client = SimpleNamespace(
        user_assigned_identities=SimpleNamespace(
            delete=lambda **kwargs: identity_delete_poller
        )
    )
    monkeypatch.setattr(
        identity_module,
        "AuthorizationManagementClient",
        lambda *_: SimpleNamespace(role_assignments=role_assignments),
    )
    monkeypatch.setattr(
        identity_module, "ManagedServiceIdentityClient", lambda *_: identity_client
    )
    service = MissionIdentityService(
        settings=local_settings,
        subscription_id="sub-1",
        resource_group_name="rg-1",
    )
    service._credential = object()

    await service.delete(identity_name="mission-identity", principal_id="principal-1")

    assert events == [
        "role:assignment-acr",
        "role:assignment-foundry",
        "identity:mission-identity",
    ]


@pytest.mark.asyncio
async def test_partial_role_assignment_failure_rolls_back_roles_before_identity(
    monkeypatch, local_settings
):
    import app.deploy_launch.mission_identity_service as identity_module

    events: list[str] = []
    identity = SimpleNamespace(
        id="/subscriptions/sub-1/resourceGroups/rg-1/providers/"
        "Microsoft.ManagedIdentity/userAssignedIdentities/mission",
        principal_id="principal-1",
        client_id="client-1",
    )
    identity_client = SimpleNamespace(
        user_assigned_identities=SimpleNamespace(
            create_or_update=lambda **kwargs: identity,
            delete=lambda **kwargs: SimpleNamespace(
                result=lambda: events.append("identity")
            ),
        )
    )
    create_calls = 0

    def create_assignment(**kwargs):
        nonlocal create_calls
        create_calls += 1
        if create_calls == 2:
            raise RuntimeError("second assignment failed")
        return SimpleNamespace(id="assignment-first")

    role_assignments = SimpleNamespace(
        create=create_assignment,
        list_for_subscription=lambda **kwargs: [
            SimpleNamespace(id="assignment-first")
        ],
        delete_by_id=lambda assignment_id: events.append(f"role:{assignment_id}"),
    )
    monkeypatch.setattr(
        identity_module, "ManagedServiceIdentityClient", lambda *_: identity_client
    )
    monkeypatch.setattr(
        identity_module,
        "AuthorizationManagementClient",
        lambda *_: SimpleNamespace(role_assignments=role_assignments),
    )
    service = MissionIdentityService(
        settings=local_settings,
        subscription_id="sub-1",
        resource_group_name="rg-1",
    )
    service._credential = object()

    with pytest.raises(
        identity_module.MissionIdentityProvisioningError,
        match="second assignment failed",
    ):
        await service.provision(
            mission_id="mission-1",
            foundry_account_id="/subscriptions/sub-1/providers/foundry/account",
            acr_id="/subscriptions/sub-1/providers/acr/registry",
        )

    assert events == ["role:assignment-first", "identity"]
