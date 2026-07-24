"""Personal Agent Memory repository abstraction.

Isolated behind a protocol so the storage backend (in-memory for Phase 4;
Cosmos DB / Azure SQL per the Architecture Principles in
``.github/copilot-instructions.md`` for a later phase) can change without
touching ``PersonalMemoryStore`` or any caller.
"""
from __future__ import annotations

import asyncio
from typing import Protocol

from app.memory.memory_models import PersonalMemoryRecord


class PersonalMemoryRepository(Protocol):
    """Per-session, per-agent isolated storage for Personal Agent Memory."""

    async def put(self, record: PersonalMemoryRecord) -> None:
        """Insert or replace a record by its id."""
        ...

    async def get(
        self, *, session_id: str, agent_id: str, record_id: str
    ) -> PersonalMemoryRecord | None:
        """Fetch a single record, or ``None`` if it does not exist."""
        ...

    async def list_for_agent(
        self, *, session_id: str, agent_id: str
    ) -> list[PersonalMemoryRecord]:
        """List every record owned by ``agent_id`` within ``session_id``."""
        ...


class InMemoryPersonalMemoryRepository:
    """Process-local ``PersonalMemoryRepository`` for local development and tests.

    Not suitable for production (state is not durable or shared across
    instances); production must configure a real backend (see
    ``Settings.memory_store_backend``).
    """

    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str], PersonalMemoryRecord] = {}
        self._lock = asyncio.Lock()

    async def put(self, record: PersonalMemoryRecord) -> None:
        async with self._lock:
            key = (record.session_id, record.agent_id, record.id)
            self._records[key] = record

    async def get(
        self, *, session_id: str, agent_id: str, record_id: str
    ) -> PersonalMemoryRecord | None:
        async with self._lock:
            return self._records.get((session_id, agent_id, record_id))

    async def list_for_agent(
        self, *, session_id: str, agent_id: str
    ) -> list[PersonalMemoryRecord]:
        async with self._lock:
            return [
                record
                for (rec_session_id, rec_agent_id, _), record in self._records.items()
                if rec_session_id == session_id and rec_agent_id == agent_id
            ]
