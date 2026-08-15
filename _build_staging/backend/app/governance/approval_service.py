"""Approval framework service.

Implements the "APPROVAL FRAMEWORK" and "SESSION REPLAY" requirements for
Phase 5: approval checkpoints are loaded from externally configured policy
(``config/policies/approval_policy.yaml``, never hardcoded); requests
support pending/approved/rejected/expired states; every state transition is
captured as an ``ApprovalAuditRecord`` for full auditability and session
replay reconstruction.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config.settings import Settings
from app.governance.governance_service import GovernanceService
from app.models.approval_models import (
    ApprovalAuditEvent,
    ApprovalAuditRecord,
    ApprovalCheckpoint,
    ApprovalDecision,
    ApprovalDecisionOutcome,
    ApprovalRequest,
)
from app.repositories.approval_repository import ApprovalRepository, InMemoryApprovalRepository
from app.utils.yaml_loader import YamlLoadError, load_yaml_file

_DEFAULT_POLICY_FILENAME = "approval_policy.yaml"

__all__ = [
    "ApprovalAlreadyDecidedError",
    "ApprovalExpiredError",
    "ApprovalPolicyDocument",
    "ApprovalPolicyError",
    "ApprovalService",
    "UnknownApprovalCheckpointError",
    "UnknownApprovalRequestError",
    "load_approval_policy",
]


class ApprovalPolicyError(RuntimeError):
    """Raised when the approval policy configuration is missing or invalid."""


class UnknownApprovalCheckpointError(RuntimeError):
    """Raised when a request references a checkpoint not defined in policy."""


class UnknownApprovalRequestError(RuntimeError):
    """Raised when a decision references an unknown approval request."""


class ApprovalAlreadyDecidedError(RuntimeError):
    """Raised when attempting to decide a request that is no longer pending."""


class ApprovalExpiredError(RuntimeError):
    """Raised when attempting to decide a request that has expired."""


class ApprovalPolicyDocument(BaseModel):
    """The full, required approval policy document."""

    model_config = ConfigDict(extra="forbid")

    default_expiry_minutes: int = Field(gt=0)
    checkpoints: list[ApprovalCheckpoint] = Field(min_length=1)


def load_approval_policy(
    policies_path: Path, filename: str = _DEFAULT_POLICY_FILENAME
) -> ApprovalPolicyDocument:
    """Load and validate the approval policy file. Fails closed on any problem."""

    path = policies_path / filename
    if not path.is_file():
        raise ApprovalPolicyError(f"Approval policy file '{path}' does not exist.")

    try:
        raw = load_yaml_file(path)
    except YamlLoadError as exc:
        raise ApprovalPolicyError(str(exc)) from exc

    if not isinstance(raw, dict):
        raise ApprovalPolicyError(f"Approval policy file '{path}' must define a top-level mapping.")

    try:
        return ApprovalPolicyDocument.model_validate(raw)
    except ValidationError as exc:
        raise ApprovalPolicyError(f"Invalid approval policy in '{path}': {exc}") from exc


class ApprovalService:
    """Manages approval checkpoints, requests, decisions, and audit records."""

    def __init__(
        self,
        *,
        repository: ApprovalRepository,
        policy: ApprovalPolicyDocument,
        governance_service: GovernanceService | None = None,
    ) -> None:
        self._repository = repository
        self._policy = policy
        self._checkpoints = {checkpoint.id: checkpoint for checkpoint in policy.checkpoints}
        self._governance_service = governance_service

    @property
    def policy(self) -> ApprovalPolicyDocument:
        return self._policy

    def checkpoint(self, checkpoint_id: str) -> ApprovalCheckpoint:
        try:
            return self._checkpoints[checkpoint_id]
        except KeyError as exc:
            raise UnknownApprovalCheckpointError(
                f"Unknown approval checkpoint id '{checkpoint_id}'."
            ) from exc

    async def request_approval(
        self,
        *,
        checkpoint_id: str,
        session_id: str,
        trace_id: str,
        requested_by_agent_id: str,
        subject_type: str,
        subject_id: str,
    ) -> ApprovalRequest:
        checkpoint = self.checkpoint(checkpoint_id)
        now = datetime.now(UTC)
        request = ApprovalRequest(
            id=str(uuid4()),
            checkpoint_id=checkpoint.id,
            session_id=session_id,
            trace_id=trace_id,
            requested_by_agent_id=requested_by_agent_id,
            subject_type=subject_type,
            subject_id=subject_id,
            status="pending",
            requested_at=now,
            expires_at=now + timedelta(minutes=self._policy.default_expiry_minutes),
        )
        await self._repository.put_request(request)
        await self._audit(request, event="requested", actor=requested_by_agent_id, detail="")
        return request

    async def decide(
        self,
        *,
        request_id: str,
        decision: ApprovalDecisionOutcome,
        decided_by: str,
        rationale: str = "",
    ) -> ApprovalDecision:
        request = await self._get_request_expiring_if_needed(request_id)

        if request.status == "expired":
            raise ApprovalExpiredError(f"Approval request '{request_id}' has expired.")
        if request.status != "pending":
            raise ApprovalAlreadyDecidedError(
                f"Approval request '{request_id}' has already been decided "
                f"(status='{request.status}')."
            )

        now = datetime.now(UTC)
        decision_record = ApprovalDecision(
            id=str(uuid4()),
            request_id=request.id,
            decision=decision,
            decided_by=decided_by,
            decided_at=now,
            rationale=rationale,
        )
        updated_request = request.model_copy(update={"status": decision})
        await self._repository.put_request(updated_request)
        await self._repository.put_decision(decision_record)
        await self._audit(updated_request, event=decision, actor=decided_by, detail=rationale)
        return decision_record

    async def expire_pending(self, *, session_id: str) -> list[ApprovalRequest]:
        """Mark every overdue pending request in ``session_id`` as expired."""

        expired: list[ApprovalRequest] = []
        for request in await self._repository.list_requests_for_session(session_id=session_id):
            maybe_expired = await self._get_request_expiring_if_needed(request.id)
            if maybe_expired.status == "expired" and request.status == "pending":
                expired.append(maybe_expired)
        return expired

    async def get_request(self, request_id: str) -> ApprovalRequest | None:
        return await self._repository.get_request(request_id=request_id)

    async def list_requests_for_session(self, session_id: str) -> list[ApprovalRequest]:
        return await self._repository.list_requests_for_session(session_id=session_id)

    async def list_decisions_for_session(self, session_id: str) -> list[ApprovalDecision]:
        requests = await self._repository.list_requests_for_session(session_id=session_id)
        decisions: list[ApprovalDecision] = []
        for request in requests:
            decisions.extend(await self._repository.list_decisions_for_request(request_id=request.id))
        return decisions

    async def audit_trail(self, *, session_id: str) -> list[ApprovalAuditRecord]:
        return await self._repository.list_audit_for_session(session_id=session_id)

    async def _get_request_expiring_if_needed(self, request_id: str) -> ApprovalRequest:
        request = await self._repository.get_request(request_id=request_id)
        if request is None:
            raise UnknownApprovalRequestError(f"Unknown approval request id '{request_id}'.")

        if (
            request.status == "pending"
            and request.expires_at is not None
            and datetime.now(UTC) > request.expires_at
        ):
            expired_request = request.model_copy(update={"status": "expired"})
            await self._repository.put_request(expired_request)
            await self._audit(expired_request, event="expired", actor="system", detail="")
            return expired_request

        return request

    async def _audit(
        self, request: ApprovalRequest, *, event: ApprovalAuditEvent, actor: str, detail: str
    ) -> None:
        record = ApprovalAuditRecord(
            id=str(uuid4()),
            request_id=request.id,
            session_id=request.session_id,
            trace_id=request.trace_id,
            event=event,
            timestamp=datetime.now(UTC),
            actor=actor,
            detail=detail,
        )
        await self._repository.put_audit_record(record)
        if self._governance_service is not None:
            await self._governance_service.record_approval_audit(record)


def create_approval_service(
    *,
    settings: Settings,
    repository: ApprovalRepository | None = None,
    governance_service: GovernanceService | None = None,
) -> ApprovalService:
    """Build an ``ApprovalService`` wired to the externally configured approval policy.

    Raises ``ApprovalPolicyError`` (fail closed) if the approval policy
    configuration is missing or invalid.
    """

    policy = load_approval_policy(settings.policies_path)
    resolved_repository = repository or InMemoryApprovalRepository()
    return ApprovalService(
        repository=resolved_repository, policy=policy, governance_service=governance_service
    )
