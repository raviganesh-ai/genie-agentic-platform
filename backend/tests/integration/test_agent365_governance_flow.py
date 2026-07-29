"""Integration tests for the Agent365 / local governance provider flow."""
from __future__ import annotations

import pytest

from app.governance.governance_models import Agent365GovernanceProvider, GovernanceProviderError
from app.governance.governance_service import create_governance_service
from app.models.approval_models import ApprovalAuditRecord
from app.models.governance_event import GovernanceEvent


class _FakeAgent365Provider:
    """A minimal stand-in satisfying the Agent365GovernanceProvider Protocol.

    Represents what a real Agent365 SDK-backed implementation would need to
    provide; Genie does not ship one (see governance_models.py docstring).
    """

    def __init__(self) -> None:
        self.recorded_events: list[GovernanceEvent] = []
        self.recorded_audit: list[ApprovalAuditRecord] = []

    def record(self, event: GovernanceEvent) -> None:
        self.recorded_events.append(event)

    def record_approval_audit(self, record: ApprovalAuditRecord) -> None:
        self.recorded_audit.append(record)


def test_fake_provider_satisfies_agent365_protocol():
    provider: Agent365GovernanceProvider = _FakeAgent365Provider()
    assert isinstance(provider, object)  # structural typing - no runtime isinstance check needed


@pytest.mark.asyncio
async def test_local_mode_defaults_to_local_governance_trace_provider(local_settings):
    service = create_governance_service(settings=local_settings)

    await service.record_execution(session_id="session-1", trace_id="trace-1", agent_id="agent-a")

    events = await service.events_for_session("session-1")
    assert len(events) == 1
    assert events[0].category == "agent_execution"


@pytest.mark.asyncio
async def test_production_requires_explicit_provider(production_settings):
    with pytest.raises(GovernanceProviderError):
        create_governance_service(settings=production_settings)


@pytest.mark.asyncio
async def test_production_with_explicit_provider_records_events(production_settings):
    provider = _FakeAgent365Provider()
    service = create_governance_service(settings=production_settings, provider=provider)

    await service.record_memory_write(
        session_id="session-1", trace_id="trace-1", agent_id="agent-a", detail={"tier": "shared"}
    )

    assert len(provider.recorded_events) == 1
    assert provider.recorded_events[0].category == "memory_write"

    events = await service.events_for_session("session-1")
    assert len(events) == 1


@pytest.mark.asyncio
async def test_all_tracked_categories_are_recorded(local_settings):
    service = create_governance_service(settings=local_settings)

    await service.record_agent_registration(session_id="s1", trace_id="t1", agent_id="agent-a")
    await service.record_agent_version(session_id="s1", trace_id="t1", agent_id="agent-a", version="1.0.0")
    await service.record_lifecycle_event(session_id="s1", trace_id="t1", agent_id="agent-a", state="Analyzing")
    await service.record_execution(session_id="s1", trace_id="t1", agent_id="agent-a")
    await service.record_communication(
        session_id="s1", trace_id="t1", agent_id="agent-a", target_agent_id="agent-b"
    )
    await service.record_memory_read(session_id="s1", trace_id="t1", agent_id="agent-a")
    await service.record_memory_write(session_id="s1", trace_id="t1", agent_id="agent-a")
    await service.record_tool_request(session_id="s1", trace_id="t1", agent_id="agent-a", tool_name="search")
    await service.record_policy_evaluation(
        session_id="s1", trace_id="t1", agent_id="agent-a", policy_name="memory_access", allowed=True
    )
    await service.record_access_denied(session_id="s1", trace_id="t1", agent_id="agent-a", reason="no access")
    await service.record_human_checkpoint_confirmation(
        session_id="s1",
        trace_id="t1",
        stage_key="requirements",
        stage_label="Requirement Discovery",
        confirmed_by="user-1",
    )
    await service.record_risk_acceptance(
        session_id="s1",
        trace_id="t1",
        workflow_run_id="run-1",
        justification="Findings are low severity and will be fixed post-launch.",
        accepted_finding_ids=["sec-1"],
        accepted_by="user-1",
    )

    events = await service.events_for_session("s1")
    categories = {event.category for event in events}
    assert categories == {
        "agent_registration",
        "agent_version",
        "agent_lifecycle",
        "agent_execution",
        "agent_communication",
        "memory_read",
        "memory_write",
        "tool_request",
        "policy_evaluation",
        "access_denied",
        "human_checkpoint_confirmation",
        "risk_accepted",
    }


@pytest.mark.asyncio
async def test_human_checkpoint_confirmation_records_stage_and_confirming_user(local_settings):
    service = create_governance_service(settings=local_settings)

    event = await service.record_human_checkpoint_confirmation(
        session_id="s1",
        trace_id="t1",
        stage_key="architecture-studio",
        stage_label="Architecture Studio",
        confirmed_by="user-42",
    )

    assert event.category == "human_checkpoint_confirmation"
    assert event.agent_id is None
    assert event.detail == {
        "stage_key": "architecture-studio",
        "stage_label": "Architecture Studio",
        "confirmed_by": "user-42",
    }

    events = await service.events_for_session("s1")
    assert events == [event]


@pytest.mark.asyncio
async def test_record_risk_acceptance_records_workflow_run_and_accepting_user(local_settings):
    service = create_governance_service(settings=local_settings)

    event = await service.record_risk_acceptance(
        session_id="s1",
        trace_id="t1",
        workflow_run_id="run-1",
        justification="Accepted for prototype demo; will remediate before GA.",
        accepted_finding_ids=["sec-1", "test-1"],
        accepted_by="user-42",
    )

    assert event.category == "risk_accepted"
    assert event.agent_id is None
    assert event.detail == {
        "workflow_run_id": "run-1",
        "justification": "Accepted for prototype demo; will remediate before GA.",
        "accepted_finding_ids": ["sec-1", "test-1"],
        "accepted_by": "user-42",
    }


@pytest.mark.asyncio
async def test_has_risk_acceptance_is_false_until_recorded_for_that_workflow_run(local_settings):
    service = create_governance_service(settings=local_settings)

    assert await service.has_risk_acceptance(session_id="s1", workflow_run_id="run-1") is False

    await service.record_risk_acceptance(
        session_id="s1",
        trace_id="t1",
        workflow_run_id="run-1",
        justification="Accepted.",
        accepted_finding_ids=[],
        accepted_by="user-1",
    )

    assert await service.has_risk_acceptance(session_id="s1", workflow_run_id="run-1") is True
    assert await service.has_risk_acceptance(session_id="s1", workflow_run_id="run-2") is False
