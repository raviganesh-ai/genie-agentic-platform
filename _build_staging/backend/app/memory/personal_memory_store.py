"""Personal Agent Memory store.

Enforces access policy (readable only by the owning agent unless policy
permits otherwise), records full decision lineage on every write, and
emits governance events for every read/write/denial. Contains no agent
reasoning of its own - callers (Foundry-hosted agents, via the future
AgentOrchestrator in Phase 6) supply already-produced observations,
findings, and work products as opaque content.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.agents.models import AgentDefinition
from app.memory.memory_access_policy_service import MemoryAccessPolicyService
from app.memory.memory_events import (
    MemoryEventType,
    MemoryGovernanceEvent,
    MemoryGovernanceRecorder,
    NullMemoryGovernanceRecorder,
)
from app.memory.memory_models import (
    MemoryAccessDeniedError,
    MemoryLineage,
    PersonalMemoryClassification,
    PersonalMemoryRecord,
)
from app.repositories.personal_memory_repository import PersonalMemoryRepository


class PersonalMemoryStore:
    """Reads and writes Personal Agent Memory with policy and governance enforcement."""

    def __init__(
        self,
        repository: PersonalMemoryRepository,
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
        classification: PersonalMemoryClassification,
        content: dict[str, Any],
        evidence_references: list[str] | None = None,
        confidence_score: float = 1.0,
    ) -> PersonalMemoryRecord:
        decision = self._policy_service.authorize_personal_write(agent=agent)
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
            approval_status="not_required",
        )
        record = PersonalMemoryRecord(
            id=str(uuid4()),
            session_id=session_id,
            agent_id=agent.id,
            classification=classification,
            content=content,
            lineage=lineage,
        )
        await self._repository.put(record)
        self._emit(
            event_type="memory_write",
            session_id=session_id,
            agent_id=agent.id,
            trace_id=trace_id,
            detail=f"classification={classification}",
        )
        return record

    async def read(
        self,
        *,
        requesting_agent: AgentDefinition,
        owning_agent_id: str,
        session_id: str,
        trace_id: str,
    ) -> list[PersonalMemoryRecord]:
        decision = self._policy_service.authorize_personal_read(
            requesting_agent_id=requesting_agent.id, owning_agent_id=owning_agent_id
        )
        if not decision.allowed:
            self._emit(
                event_type="memory_denied",
                session_id=session_id,
                agent_id=requesting_agent.id,
                trace_id=trace_id,
                detail=decision.reason,
            )
            raise MemoryAccessDeniedError(decision.reason)

        records = await self._repository.list_for_agent(session_id=session_id, agent_id=owning_agent_id)
        self._emit(
            event_type="memory_read",
            session_id=session_id,
            agent_id=requesting_agent.id,
            trace_id=trace_id,
            detail=f"count={len(records)}",
        )
        return records

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
                tier="personal",
                session_id=session_id,
                agent_id=agent_id,
                trace_id=trace_id,
                timestamp=datetime.now(UTC),
                detail=detail,
            )
        )
