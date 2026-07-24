"""Unit tests for ApprovalService."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.governance.approval_service import (
    ApprovalAlreadyDecidedError,
    ApprovalExpiredError,
    ApprovalPolicyDocument,
    ApprovalService,
    UnknownApprovalCheckpointError,
    UnknownApprovalRequestError,
)
from app.models.approval_models import ApprovalCheckpoint, ApprovalRequest
from app.repositories.approval_repository import InMemoryApprovalRepository


def _policy(default_expiry_minutes: int = 1440) -> ApprovalPolicyDocument:
    return ApprovalPolicyDocument(
        default_expiry_minutes=default_expiry_minutes,
        checkpoints=[
            ApprovalCheckpoint(
                id="architecture-approval",
                name="Architecture Approval",
                description="Approve the proposed architecture.",
            )
        ],
    )


def _service(default_expiry_minutes: int = 1440) -> tuple[ApprovalService, InMemoryApprovalRepository]:
    repository = InMemoryApprovalRepository()
    return ApprovalService(repository=repository, policy=_policy(default_expiry_minutes)), repository


@pytest.mark.asyncio
async def test_request_approval_creates_pending_request():
    service, _ = _service()
    request = await service.request_approval(
        checkpoint_id="architecture-approval",
        session_id="session-1",
        trace_id="trace-1",
        requested_by_agent_id="architecture-designer",
        subject_type="architecture",
        subject_id="arch-1",
    )
    assert request.status == "pending"
    assert request.checkpoint_id == "architecture-approval"

    audit = await service.audit_trail(session_id="session-1")
    assert any(record.event == "requested" for record in audit)


@pytest.mark.asyncio
async def test_request_approval_with_unknown_checkpoint_raises():
    service, _ = _service()
    with pytest.raises(UnknownApprovalCheckpointError):
        await service.request_approval(
            checkpoint_id="does-not-exist",
            session_id="session-1",
            trace_id="trace-1",
            requested_by_agent_id="agent-a",
            subject_type="architecture",
            subject_id="arch-1",
        )


@pytest.mark.asyncio
async def test_decide_approved_updates_status_and_audit():
    service, _ = _service()
    request = await service.request_approval(
        checkpoint_id="architecture-approval",
        session_id="session-1",
        trace_id="trace-1",
        requested_by_agent_id="agent-a",
        subject_type="architecture",
        subject_id="arch-1",
    )

    decision = await service.decide(
        request_id=request.id, decision="approved", decided_by="reviewer-1", rationale="Looks good."
    )
    assert decision.decision == "approved"

    updated = await service.get_request(request.id)
    assert updated.status == "approved"

    audit = await service.audit_trail(session_id="session-1")
    assert [record.event for record in audit] == ["requested", "approved"]


@pytest.mark.asyncio
async def test_decide_unknown_request_raises():
    service, _ = _service()
    with pytest.raises(UnknownApprovalRequestError):
        await service.decide(request_id="unknown", decision="approved", decided_by="reviewer-1")


@pytest.mark.asyncio
async def test_decide_twice_raises_already_decided():
    service, _ = _service()
    request = await service.request_approval(
        checkpoint_id="architecture-approval",
        session_id="session-1",
        trace_id="trace-1",
        requested_by_agent_id="agent-a",
        subject_type="architecture",
        subject_id="arch-1",
    )
    await service.decide(request_id=request.id, decision="approved", decided_by="reviewer-1")

    with pytest.raises(ApprovalAlreadyDecidedError):
        await service.decide(request_id=request.id, decision="rejected", decided_by="reviewer-2")


@pytest.mark.asyncio
async def test_decide_expired_request_raises_and_audits_expiry():
    service, repository = _service()
    now = datetime.now(UTC)
    expired_request = ApprovalRequest(
        id="req-expired",
        checkpoint_id="architecture-approval",
        session_id="session-1",
        trace_id="trace-1",
        requested_by_agent_id="agent-a",
        subject_type="architecture",
        subject_id="arch-1",
        status="pending",
        requested_at=now - timedelta(minutes=10),
        expires_at=now - timedelta(minutes=1),
    )
    await repository.put_request(expired_request)

    with pytest.raises(ApprovalExpiredError):
        await service.decide(request_id="req-expired", decision="approved", decided_by="reviewer-1")

    updated = await service.get_request("req-expired")
    assert updated.status == "expired"

    audit = await service.audit_trail(session_id="session-1")
    assert any(record.event == "expired" for record in audit)


@pytest.mark.asyncio
async def test_expire_pending_marks_overdue_requests():
    service, repository = _service()
    now = datetime.now(UTC)
    overdue_request = ApprovalRequest(
        id="req-overdue",
        checkpoint_id="architecture-approval",
        session_id="session-1",
        trace_id="trace-1",
        requested_by_agent_id="agent-a",
        subject_type="architecture",
        subject_id="arch-1",
        status="pending",
        requested_at=now - timedelta(minutes=10),
        expires_at=now - timedelta(minutes=1),
    )
    await repository.put_request(overdue_request)

    expired = await service.expire_pending(session_id="session-1")
    assert [request.id for request in expired] == ["req-overdue"]

    updated = await service.get_request("req-overdue")
    assert updated.status == "expired"
