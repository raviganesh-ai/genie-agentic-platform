"""Agent handoff service.

Implements the "AGENT HANDOFF MODEL" for Phase 6: records every control
handoff from one agent to another within a workflow run and emits a
matching governance ``agent_communication`` event (Phase 5
``GovernanceService``, unmodified).
"""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.governance.governance_service import GovernanceService
from app.models.handoff_models import AgentHandoff

__all__ = ["HandoffService"]


class HandoffService:
    """Records and queries ``AgentHandoff`` events for workflow runs."""

    def __init__(self, *, governance_service: GovernanceService) -> None:
        self._governance_service = governance_service
        self._handoffs: dict[str, list[AgentHandoff]] = {}

    async def record_handoff(
        self,
        *,
        source_agent_id: str,
        target_agent_id: str,
        session_id: str,
        workflow_run_id: str,
        trace_id: str,
        reason: str,
        evidence_references: list[str] | None = None,
    ) -> AgentHandoff:
        handoff = AgentHandoff(
            id=str(uuid4()),
            source_agent_id=source_agent_id,
            target_agent_id=target_agent_id,
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            trace_id=trace_id,
            reason=reason,
            evidence_references=evidence_references or [],
            timestamp=datetime.now(UTC),
        )
        self._handoffs.setdefault(workflow_run_id, []).append(handoff)

        await self._governance_service.record_communication(
            session_id=session_id,
            trace_id=trace_id,
            agent_id=source_agent_id,
            target_agent_id=target_agent_id,
            detail={"reason": reason, "workflow_run_id": workflow_run_id},
        )
        return handoff

    def handoffs_for_run(self, workflow_run_id: str) -> list[AgentHandoff]:
        return list(self._handoffs.get(workflow_run_id, []))
