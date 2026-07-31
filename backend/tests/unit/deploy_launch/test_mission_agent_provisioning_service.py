"""Unit tests for MissionAgentProvisioningService (factory + Null + extraction)."""
from __future__ import annotations

import pytest

from app.config.settings import Settings
from app.deploy_launch.mission_agent_provisioning_service import (
    MissionAgentProvisioningError,
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
    service = create_mission_agent_provisioning_service(settings=_settings(provider_mode="local"))

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


def test_production_mode_without_config_raises():
    with pytest.raises(MissionAgentProvisioningError):
        create_mission_agent_provisioning_service(settings=_settings(provider_mode="production"))


def test_extract_agent_instructions_finds_matching_paragraph():
    instructions = _extract_agent_instructions(_ARCHITECTURE_DOCUMENT, "requirements-specialist")

    assert "extracts and validates raw customer" in instructions


def test_extract_agent_instructions_falls_back_when_not_found():
    instructions = _extract_agent_instructions(_ARCHITECTURE_DOCUMENT, "totally-unknown-agent")

    assert "totally-unknown-agent" in instructions
