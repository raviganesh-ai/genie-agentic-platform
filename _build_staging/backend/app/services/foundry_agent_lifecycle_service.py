"""Foundry agent lifecycle service (Phase 10A).

Owns the single legal state machine every configured agent's Foundry
deployment moves through: draft -> registered -> provisioned -> validated,
with deprecate/retire and re-provisioning transitions. Every transition is
recorded both in-process (for fast history queries) and as a governance
lifecycle event via the existing ``GovernanceService.record_lifecycle_event``
(Phase 5) - reusing, never redesigning, the existing governance/lineage
framework.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from app.governance.governance_service import GovernanceService
from app.models.agent_lifecycle_state import AgentLifecycleState, AgentLifecycleTransition
from app.services.foundry_agent_inventory_service import FoundryAgentInventoryService

__all__ = ["FoundryAgentLifecycleService", "InvalidLifecycleTransitionError"]

_ALLOWED_TRANSITIONS: dict[AgentLifecycleState, set[AgentLifecycleState]] = {
    "draft": {"registered"},
    "registered": {"provisioned", "deprecated"},
    "provisioned": {"validated", "deprecated"},
    "validated": {"provisioned", "deprecated"},
    "deprecated": {"provisioned", "retired"},
    "retired": set(),
}


class InvalidLifecycleTransitionError(RuntimeError):
    """Raised when a requested lifecycle transition is not legal from the current state."""


class FoundryAgentLifecycleService:
    """Tracks and governs the current lifecycle state of every configured agent."""

    def __init__(
        self,
        *,
        governance_service: GovernanceService,
        inventory_service: FoundryAgentInventoryService,
    ) -> None:
        self._governance_service = governance_service
        self._inventory_service = inventory_service
        self._current_state: dict[str, AgentLifecycleState] = {}
        self._history: dict[str, list[AgentLifecycleTransition]] = {}
        self._lock = asyncio.Lock()

    async def current_state(self, agent_id: str) -> AgentLifecycleState:
        async with self._lock:
            return self._current_state.get(agent_id, "draft")

    async def history(self, agent_id: str) -> list[AgentLifecycleTransition]:
        async with self._lock:
            return list(self._history.get(agent_id, []))

    async def transition(
        self,
        *,
        agent_id: str,
        to_state: AgentLifecycleState,
        reason: str,
        session_id: str,
        trace_id: str,
    ) -> AgentLifecycleTransition:
        """Move ``agent_id`` to ``to_state``. Fails closed on an illegal transition."""

        async with self._lock:
            from_state = self._current_state.get(agent_id, "draft")
            if to_state != from_state and to_state not in _ALLOWED_TRANSITIONS.get(
                from_state, set()
            ):
                raise InvalidLifecycleTransitionError(
                    f"Cannot transition agent '{agent_id}' from '{from_state}' to "
                    f"'{to_state}'."
                )

            transition = AgentLifecycleTransition(
                id=str(uuid4()),
                agent_id=agent_id,
                from_state=from_state,
                to_state=to_state,
                reason=reason,
                session_id=session_id,
                trace_id=trace_id,
                occurred_at=datetime.now(UTC),
            )
            self._current_state[agent_id] = to_state
            self._history.setdefault(agent_id, []).append(transition)

        await self._governance_service.record_lifecycle_event(
            session_id=session_id, trace_id=trace_id, agent_id=agent_id, state=to_state
        )
        await self._inventory_service.record_lifecycle_state(agent_id, to_state)
        return transition
