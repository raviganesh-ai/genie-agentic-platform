"""Shared Collaboration Memory repository abstraction.

Isolated behind a protocol so the storage backend (in-memory for Phase 4;
Cosmos DB / Azure SQL for a later phase) can change without touching
``SharedMemoryStore`` or any caller.
"""
from __future__ import annotations

import asyncio
from typing import Protocol

from app.memory.memory_models import SharedMemoryRecord


class SharedMemoryRepository(Protocol):
    """Session-scoped storage for Shared Collaboration Memory."""

    async def get(self, *, session_id: str, key: str) -> SharedMemoryRecord | None:
        """Fetch the current record for ``key`` within ``session_id``, if any."""
        ...

    async def put(self, record: SharedMemoryRecord) -> None:
        """Insert or replace the record for its ``(session_id, id)`` key."""
        ...

    async def list_for_session(self, *, session_id: str) -> list[SharedMemoryRecord]:
        """List every record within ``session_id``."""
        ...

    async def delete_prefix(self, *, session_id: str, key_prefix: str) -> int:
        """Delete records in one session whose keys begin with ``key_prefix``."""
        ...


class InMemorySharedMemoryRepository:
    """Process-local ``SharedMemoryRepository`` for local development and tests.

    Not suitable for production (state is not durable or shared across
    instances); production must configure a real backend (see
    ``Settings.memory_store_backend``).
    """

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], SharedMemoryRecord] = {}
        self._lock = asyncio.Lock()

    async def get(self, *, session_id: str, key: str) -> SharedMemoryRecord | None:
        async with self._lock:
            return self._records.get((session_id, key))

    async def put(self, record: SharedMemoryRecord) -> None:
        async with self._lock:
            self._records[(record.session_id, record.id)] = record

    async def list_for_session(self, *, session_id: str) -> list[SharedMemoryRecord]:
        async with self._lock:
            return [
                record
                for (rec_session_id, _), record in self._records.items()
                if rec_session_id == session_id
            ]

    async def delete_prefix(self, *, session_id: str, key_prefix: str) -> int:
        async with self._lock:
            keys = [
                key
                for key in self._records
                if key[0] == session_id and key[1].startswith(key_prefix)
            ]
            for key in keys:
                del self._records[key]
            return len(keys)
