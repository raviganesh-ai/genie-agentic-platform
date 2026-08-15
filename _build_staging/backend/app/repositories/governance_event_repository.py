"""Governance event repository abstraction.

Isolated behind a protocol so the storage backend (in-memory for Phase 5;
Cosmos DB / Azure SQL for a later phase, per ``Settings.lineage_store_backend``)
can change without touching ``GovernanceService`` or any caller.
"""
from __future__ import annotations

import asyncio
from typing import Protocol

from app.models.governance_event import GovernanceEvent


class GovernanceEventRepository(Protocol):
    """Append-only, session-queryable storage for governance events."""

    async def append(self, event: GovernanceEvent) -> None:
        """Persist a single governance event."""
        ...

    async def list_for_session(self, *, session_id: str) -> list[GovernanceEvent]:
        """List every event recorded for ``session_id``, in insertion order."""
        ...


class InMemoryGovernanceEventRepository:
    """Process-local ``GovernanceEventRepository`` for local development and tests.

    Not suitable for production (state is not durable or shared across
    instances); production must configure a real backend (see
    ``Settings.lineage_store_backend``).
    """

    def __init__(self) -> None:
        self._events: list[GovernanceEvent] = []
        self._lock = asyncio.Lock()

    async def append(self, event: GovernanceEvent) -> None:
        async with self._lock:
            self._events.append(event)

    async def list_for_session(self, *, session_id: str) -> list[GovernanceEvent]:
        async with self._lock:
            return [event for event in self._events if event.session_id == session_id]
