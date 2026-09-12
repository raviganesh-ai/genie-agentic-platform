"""Shared Collaboration Memory store.

Enforces governed writes and "no overwrite without approval": any write to
an existing key requires the incoming lineage's ``approval_status`` to be
``"approved"`` when the policy's ``require_approval_for_overwrite`` is set
(the seeded default). Emits governance events for every read/write/update/
denial, controlled by the policy's ``emits_governance_events`` flag.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.agents.models import AgentDefinition
from app.memory.memory_access_policy_service import MemoryAccessPolicyService
from app.memory.memory_events import (
    MemoryEventType,
    MemoryGovernanceEvent,
    MemoryGovernanceRecorder,
    NullMemoryGovernanceRecorder,
)
from app.memory.memory_models import (
    ApprovalStatus,
    MemoryAccessDeniedError,
    MemoryLineage,
    SharedMemoryClassification,
    SharedMemoryRecord,
)
from app.repositories.shared_memory_repository import SharedMemoryRepository


class SharedMemoryStore:
    """Reads and writes Shared Collaboration Memory with policy and governance enforcement."""

    def __init__(
        self,
        repository: SharedMemoryRepository,
        policy_service: MemoryAccessPolicyService,
        governance_recorder: MemoryGovernanceRecorder | None = None,
    ) -> None:
        self._repository = repository
        self._policy_service = policy_service
        self._governance_recorder = governance_recorder or NullMemoryGovernanceRecorder()

    async def write(
        self,
        *,
        agent: AgentDefinition,
        session_id: str,
        trace_id: str,
        key: str,
        classification: SharedMemoryClassification,
        content: dict[str, Any],
        approval_status: ApprovalStatus = "not_required",
        evidence_references: list[str] | None = None,
        confidence_score: float = 1.0,
    ) -> SharedMemoryRecord:
        existing = await self._repository.get(session_id=session_id, key=key)
        is_overwrite = existing is not None

        decision = self._policy_service.authorize_shared_write(
            agent=agent, is_overwrite=is_overwrite, approval_status=approval_status
        )
        if not decision.allowed:
            self._emit(
                event_type="memory_denied",
                session_id=session_id,
                agent_id=agent.id,
                trace_id=trace_id,
                detail=decision.reason,
            )
            raise MemoryAccessDeniedError(decision.reason)

        lineage = MemoryLineage(
            session_id=session_id,
            trace_id=trace_id,
            agent_id=agent.id,
            agent_version=agent.version,
            timestamp=datetime.now(UTC),
            evidence_references=evidence_references or [],
            confidence_score=confidence_score,
            approval_status=approval_status,
        )
        record = SharedMemoryRecord(
            id=key,
            session_id=session_id,
            classification=classification,
            content=content,
            lineage=lineage,
            version=(existing.version + 1) if existing else 1,
        )
        await self._repository.put(record)

        if self._policy_service.document.shared_collaboration_memory.emits_governance_events:
            self._emit(
                event_type="memory_update" if is_overwrite else "memory_write",
                session_id=session_id,
                agent_id=agent.id,
                trace_id=trace_id,
                detail=f"key={key} version={record.version}",
            )
        return record

    async def read(
        self,
        *,
        requesting_agent: AgentDefinition,
        session_id: str,
        trace_id: str,
        key: str | None = None,
    ) -> list[SharedMemoryRecord]:
        decision = self._policy_service.authorize_shared_read(agent=requesting_agent)
        if not decision.allowed:
            self._emit(
                event_type="memory_denied",
                session_id=session_id,
                agent_id=requesting_agent.id,
                trace_id=trace_id,
                detail=decision.reason,
            )
            raise MemoryAccessDeniedError(decision.reason)

        if key is not None:
            record = await self._repository.get(session_id=session_id, key=key)
            records = [record] if record is not None else []
        else:
            records = await self._repository.list_for_session(session_id=session_id)

        if self._policy_service.document.shared_collaboration_memory.emits_governance_events:
            self._emit(
                event_type="memory_read",
                session_id=session_id,
                agent_id=requesting_agent.id,
                trace_id=trace_id,
                detail=f"count={len(records)}",
            )
        return records

    async def delete_prefix(
        self,
        *,
        agent: AgentDefinition,
        session_id: str,
        trace_id: str,
        key_prefix: str,
    ) -> int:
        """Delete one owner's artifact family after the same write-policy check."""
        decision = self._policy_service.authorize_shared_write(
            agent=agent,
            is_overwrite=True,
            approval_status="approved",
        )
        if not decision.allowed:
            self._emit(
                event_type="memory_denied",
                session_id=session_id,
                agent_id=agent.id,
                trace_id=trace_id,
                detail=decision.reason,
            )
            raise MemoryAccessDeniedError(decision.reason)
        count = await self._repository.delete_prefix(
            session_id=session_id,
            key_prefix=key_prefix,
        )
        if self._policy_service.document.shared_collaboration_memory.emits_governance_events:
            self._emit(
                event_type="memory_delete",
                session_id=session_id,
                agent_id=agent.id,
                trace_id=trace_id,
                detail=f"key_prefix={key_prefix} count={count}",
            )
        return count

    def _emit(
        self,
        *,
        event_type: MemoryEventType,
        session_id: str,
        agent_id: str,
        trace_id: str,
        detail: str,
    ) -> None:
        self._governance_recorder.record(
            MemoryGovernanceEvent(
                event_type=event_type,
                tier="shared",
                session_id=session_id,
                agent_id=agent_id,
                trace_id=trace_id,
                timestamp=datetime.now(UTC),
                detail=detail,
            )
        )
