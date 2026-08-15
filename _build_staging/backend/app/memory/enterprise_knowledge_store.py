"""Enterprise Knowledge Memory store.

Enforces reviewer-gated reads and approval-gated promotion: content is
promoted into Enterprise Knowledge Memory only when the policy's
``requires_approval_to_promote`` is satisfied by the incoming lineage's
``approval_status`` (the seeded default requires ``"approved"``), matching
"Customer data must never be promoted without approval" in the Memory
Architecture section of ``.github/copilot-instructions.md``.
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
    EnterpriseKnowledgeRecord,
    EnterpriseMemoryClassification,
    MemoryAccessDeniedError,
    MemoryLineage,
)
from app.repositories.enterprise_memory_repository import EnterpriseMemoryRepository


class EnterpriseKnowledgeStore:
    """Promotes into and searches Enterprise Knowledge Memory with policy enforcement."""

    def __init__(
        self,
        repository: EnterpriseMemoryRepository,
        policy_service: MemoryAccessPolicyService,
        governance_recorder: MemoryGovernanceRecorder | None = None,
    ) -> None:
        self._repository = repository
        self._policy_service = policy_service
        self._governance_recorder = governance_recorder or NullMemoryGovernanceRecorder()

    async def promote(
        self,
        *,
        agent: AgentDefinition,
        record_id: str,
        session_id: str,
        trace_id: str,
        classification: EnterpriseMemoryClassification,
        content: dict[str, Any],
        approval_status: ApprovalStatus,
        evidence_references: list[str] | None = None,
        confidence_score: float = 1.0,
    ) -> EnterpriseKnowledgeRecord:
        decision = self._policy_service.authorize_enterprise_write(
            agent=agent, approval_status=approval_status
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
        record = EnterpriseKnowledgeRecord(
            id=record_id, classification=classification, content=content, lineage=lineage
        )
        await self._repository.upsert(record)
        self._emit(
            event_type="memory_write",
            session_id=session_id,
            agent_id=agent.id,
            trace_id=trace_id,
            detail=f"classification={classification}",
        )
        return record

    async def search(
        self,
        *,
        agent: AgentDefinition,
        query: str,
        session_id: str,
        trace_id: str,
        top: int = 10,
    ) -> list[EnterpriseKnowledgeRecord]:
        decision = self._policy_service.authorize_enterprise_read(agent=agent)
        if not decision.allowed:
            self._emit(
                event_type="memory_denied",
                session_id=session_id,
                agent_id=agent.id,
                trace_id=trace_id,
                detail=decision.reason,
            )
            raise MemoryAccessDeniedError(decision.reason)

        results = await self._repository.search(query=query, top=top)
        self._emit(
            event_type="memory_read",
            session_id=session_id,
            agent_id=agent.id,
            trace_id=trace_id,
            detail=f"query={query!r} count={len(results)}",
        )
        return results

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
                tier="enterprise",
                session_id=session_id,
                agent_id=agent_id,
                trace_id=trace_id,
                timestamp=datetime.now(UTC),
                detail=detail,
            )
        )
