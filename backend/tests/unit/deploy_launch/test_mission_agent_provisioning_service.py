"""Unit tests for MissionAgentProvisioningService (factory + Null + extraction)."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.config.settings import Settings
from app.deploy_launch.mission_agent_provisioning_service import (
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


def test_local_mode_without_config_returns_null_service():
    service = create_mission_agent_provisioning_service(settings=_settings())

    assert isinstance(service, NullMissionAgentProvisioningService)


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

    def create_agent(
        self, *, name: str, model: str, instructions: str, description: str | None = None
    ) -> str:
        self.created.append(
            {"name": name, "model": model, "instructions": instructions, "description": description}
        )
        return f"foundry-{name}"

    def delete_agent(self, agent_id: str) -> None:  # pragma: no cover - not exercised here
        pass


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
        mission_slug="derekpoc-c753ba86",
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
    assert created_name.startswith("derekpoc-c753ba86-")
