"""Unit tests for the Phase 6 handoff service."""
from __future__ import annotations

import pytest

from app.config.settings import Settings
from app.governance.governance_service import create_governance_service
from app.orchestration.handoff_service import HandoffService


@pytest.fixture
def handoff_service(local_settings: Settings) -> HandoffService:
    governance_service = create_governance_service(settings=local_settings)
    return HandoffService(governance_service=governance_service)


async def test_record_handoff_returns_populated_agent_handoff(
    handoff_service: HandoffService,
) -> None:
    handoff = await handoff_service.record_handoff(
        source_agent_id="requirements-analyst",
        target_agent_id="architecture-designer",
        session_id="session-1",
        workflow_run_id="run-1",
        trace_id="trace-1",
        reason="Architecture design depends on completed requirements extraction.",
        evidence_references=["workflow-step-output://analyze-requirements"],
    )

    assert handoff.source_agent_id == "requirements-analyst"
    assert handoff.target_agent_id == "architecture-designer"
    assert handoff.workflow_run_id == "run-1"
    assert handoff.evidence_references == ["workflow-step-output://analyze-requirements"]


async def test_handoffs_for_run_returns_recorded_handoffs(
    handoff_service: HandoffService,
) -> None:
    await handoff_service.record_handoff(
        source_agent_id="agent-a",
        target_agent_id="agent-b",
        session_id="session-1",
        workflow_run_id="run-1",
        trace_id="trace-1",
        reason="reason-1",
    )
    await handoff_service.record_handoff(
        source_agent_id="agent-b",
        target_agent_id="agent-c",
        session_id="session-1",
        workflow_run_id="run-1",
        trace_id="trace-1",
        reason="reason-2",
    )

    handoffs = handoff_service.handoffs_for_run("run-1")

    assert [h.reason for h in handoffs] == ["reason-1", "reason-2"]
    assert handoff_service.handoffs_for_run("unknown-run") == []
