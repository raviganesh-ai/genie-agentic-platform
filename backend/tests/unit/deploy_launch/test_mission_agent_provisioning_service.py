"""Unit tests for MissionAgentProvisioningService (factory + Null + extraction)."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.config.settings import Settings
from app.deploy_launch.mission_agent_provisioning_service import (
    MissionAgentProvisioningError,
    MissionAgentProvisioningService,
    NullMissionAgentProvisioningService,
    _extract_agent_instructions,
    create_mission_agent_provisioning_service,
)

_ARCHITECTURE_DOCUMENT = """
## Multi-Agent Workflow

The requirements-specialist agent extracts and validates raw customer
requirements before anything else runs.

The orchestrator agent sequences every specialist and returns the final
result to the UI.

## UI Design

Some unrelated UI section text.
"""


def _settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[call-arg]


def test_missing_foundry_config_fails_closed_instead_of_selecting_null_service():
    with pytest.raises(MissionAgentProvisioningError, match="fake mission agent provisioning"):
        create_mission_agent_provisioning_service(settings=_settings())


async def test_null_service_provision_returns_placeholder_names():
    service = NullMissionAgentProvisioningService()

    provisioned = await service.provision(
        mission_slug="acme-mission",
        agent_names=["requirements-specialist", "orchestrator"],
        architecture_document=_ARCHITECTURE_DOCUMENT,
    )

    names = {record.agent_name: record.foundry_agent_name for record in provisioned}
    assert names["requirements-specialist"] == "local-acme-mission-requirements-specialist"
    assert names["orchestrator"] == "local-acme-mission-orchestrator"


def test_extract_agent_instructions_finds_matching_paragraph():
    instructions = _extract_agent_instructions(_ARCHITECTURE_DOCUMENT, "requirements-specialist")

    assert "extracts and validates raw customer" in instructions


def test_extract_agent_instructions_falls_back_when_not_found():
    instructions = _extract_agent_instructions(_ARCHITECTURE_DOCUMENT, "totally-unknown-agent")

    assert "totally-unknown-agent" in instructions


@dataclass
class _FakeAgentApiClient:
    created: list[dict[str, str | None]] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    unconfirmed_names: set[str] = field(default_factory=set)
    returned_names: dict[str, str] = field(default_factory=dict)

    def create_agent(
        self, *, name: str, model: str, instructions: str, description: str | None = None
    ) -> str:
        self.created.append(
            {"name": name, "model": model, "instructions": instructions, "description": description}
        )
        return self.returned_names.get(name, name)

    def agent_exists(self, agent_id: str) -> bool:
        return agent_id not in self.unconfirmed_names

    def delete_agent(self, agent_id: str) -> None:
        self.deleted.append(agent_id)


@dataclass
class _FakeProjectService:
    api_client: _FakeAgentApiClient

    def get_api_client(self) -> _FakeAgentApiClient:
        return self.api_client


async def test_provision_forwards_the_exact_agent_name_as_description():
    api_client = _FakeAgentApiClient()
    service = MissionAgentProvisioningService(
        project_service=_FakeProjectService(api_client=api_client), model_deployment_ref="gpt-4o"
    )

    await service.provision(
        mission_slug="acme-mission",
        agent_names=["Requirements Specialist", "Orchestrator"],
        architecture_document=_ARCHITECTURE_DOCUMENT,
    )

    # The exact, unslugified individual agent name must be forwarded as
    # `description` so it is visible in the Foundry portal even though the
    # resource `name` itself is a mission-slug-prefixed, slugified id.
    assert {created["description"] for created in api_client.created} == {
        "Requirements Specialist",
        "Orchestrator",
    }


async def test_provision_truncates_a_long_agent_name_to_stay_within_63_characters():
    """Regression test for a real observed Foundry failure: 'Must start and
    end with alphanumeric characters, can contain hyphens in the middle,
    and must not exceed 63 characters.' - an LLM-generated agent name (from
    the Build Agent's own "# agent: <name>" comment) can be long/descriptive
    enough that "{mission_slug}-{slugified name}" exceeds the limit."""

    api_client = _FakeAgentApiClient()
    service = MissionAgentProvisioningService(
        project_service=_FakeProjectService(api_client=api_client), model_deployment_ref="gpt-4o"
    )

    await service.provision(
        mission_slug="sample-mission-c753ba86",
        agent_names=[
            "Customer Relationship Management and Escalation Handling Specialist Agent"
        ],
        architecture_document=_ARCHITECTURE_DOCUMENT,
    )

    created_name = api_client.created[0]["name"]
    assert created_name is not None
    assert len(created_name) <= 63
    assert created_name[0].isalnum()
    assert created_name[-1].isalnum()
    assert created_name.startswith("sample-mission-c753ba86-")


async def test_provision_keeps_truncated_foundry_names_unique():
    api_client = _FakeAgentApiClient()
    service = MissionAgentProvisioningService(
        project_service=_FakeProjectService(api_client=api_client), model_deployment_ref="gpt-4o"
    )

    provisioned = await service.provision(
        mission_slug="sample-mission-c753ba86",
        agent_names=[
            "Customer Relationship Management and Escalation Handling Specialist Alpha",
            "Customer Relationship Management and Escalation Handling Specialist Beta",
        ],
        architecture_document=_ARCHITECTURE_DOCUMENT,
    )

    assert len(provisioned) == 2
    requested_names = [created["name"] for created in api_client.created]
    assert len(set(requested_names)) == 2
    assert all(name is not None and len(name) <= 63 for name in requested_names)


async def test_provision_fails_closed_and_rolls_back_when_foundry_cannot_confirm_agent():
    api_client = _FakeAgentApiClient(unconfirmed_names={"acme-mission-orchestrator"})
    service = MissionAgentProvisioningService(
        project_service=_FakeProjectService(api_client=api_client), model_deployment_ref="gpt-4o"
    )

    with pytest.raises(MissionAgentProvisioningError, match="did not confirm"):
        await service.provision(
            mission_slug="acme-mission",
            agent_names=["requirements-specialist", "orchestrator"],
            architecture_document=_ARCHITECTURE_DOCUMENT,
        )

    assert api_client.deleted == [
        "acme-mission-orchestrator",
        "acme-mission-requirements-specialist",
    ]


async def test_provision_rejects_unexpected_returned_name_and_deletes_requested_resource():
    api_client = _FakeAgentApiClient(
        returned_names={"acme-mission-orchestrator": "unexpected-resource"}
    )
    service = MissionAgentProvisioningService(
        project_service=_FakeProjectService(api_client=api_client), model_deployment_ref="gpt-4o"
    )

    with pytest.raises(MissionAgentProvisioningError, match="unexpected resource name"):
        await service.provision(
            mission_slug="acme-mission",
            agent_names=["orchestrator"],
            architecture_document=_ARCHITECTURE_DOCUMENT,
        )

    assert api_client.deleted == ["acme-mission-orchestrator"]


async def test_provision_uses_platform_default_model_when_no_override_given():
    api_client = _FakeAgentApiClient()
    service = MissionAgentProvisioningService(
        project_service=_FakeProjectService(api_client=api_client), model_deployment_ref="gpt-4o"
    )

    await service.provision(
        mission_slug="acme-mission",
        agent_names=["orchestrator"],
        architecture_document=_ARCHITECTURE_DOCUMENT,
    )

    assert api_client.created[0]["model"] == "gpt-4o"


async def test_provision_uses_approved_model_override_when_given():
    # The model the user actually selected on Genie's own Landing page for
    # this session (see pipeline_service._approved_model_deployment_ref)
    # must win over the platform default for every mission agent.
    api_client = _FakeAgentApiClient()
    service = MissionAgentProvisioningService(
        project_service=_FakeProjectService(api_client=api_client), model_deployment_ref="gpt-4o"
    )

    await service.provision(
        mission_slug="acme-mission",
        agent_names=["requirements-specialist", "orchestrator"],
        architecture_document=_ARCHITECTURE_DOCUMENT,
        model_deployment_ref="claude-opus-4",
    )

    assert {created["model"] for created in api_client.created} == {"claude-opus-4"}
