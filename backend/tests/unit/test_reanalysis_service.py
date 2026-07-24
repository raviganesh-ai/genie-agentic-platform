"""Unit tests for the Phase 6 reanalysis routing service."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.agents.registry import AgentRegistry
from app.models.reanalysis_models import ReanalysisRequest
from app.orchestration.reanalysis_service import ReanalysisRoutingError, ReanalysisService


@pytest.fixture
def agent_registry(tmp_path: Path) -> AgentRegistry:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "registry.yaml").write_text(
        "agents:\n"
        "  - id: architecture-designer\n"
        "    name: Architecture Designer\n"
        "    role: architecture_design\n"
        "    description: Designs architectures.\n"
        "    capabilities:\n"
        "      - architecture_generation\n"
        "      - alternative_design_generation\n"
        "    enabled: true\n"
        "  - id: disabled-agent\n"
        "    name: Disabled Agent\n"
        "    role: architecture_design\n"
        "    description: A disabled agent that should never be routed to.\n"
        "    capabilities:\n"
        "      - requirement_extraction\n"
        "    enabled: false\n",
        encoding="utf-8",
    )
    return AgentRegistry.load(agents_dir)


def _request(request_type: str) -> ReanalysisRequest:
    return ReanalysisRequest(
        id="request-1",
        session_id="session-1",
        workflow_run_id="run-1",
        trace_id="trace-1",
        requested_by="user-1",
        request_type=request_type,  # type: ignore[arg-type]
        requested_at=datetime.now(UTC),
    )


def test_route_finds_agent_with_matching_capability(agent_registry: AgentRegistry) -> None:
    service = ReanalysisService(agent_registry=agent_registry)

    result = service.route(_request("request_alternative_architecture"))

    assert result.status == "routed"
    assert result.routed_to_agent_id == "architecture-designer"


def test_route_raises_when_no_enabled_agent_has_capability(
    agent_registry: AgentRegistry,
) -> None:
    service = ReanalysisService(agent_registry=agent_registry)

    with pytest.raises(ReanalysisRoutingError):
        service.route(_request("challenge_recommendation"))
