"""Upload record repository abstraction.

Isolated behind a protocol so the storage backend (in-memory for local
development and tests; a durable backend for a later phase) can change
without touching ``SessionService`` or any caller.
"""
from __future__ import annotations

import asyncio
from typing import Protocol

from app.models.upload_models import UploadRecord


class UploadRepository(Protocol):
    """Storage for ``UploadRecord``s, scoped by session."""

    async def put(self, record: UploadRecord) -> None:
        """Insert or replace an upload record by its id."""
        ...

    async def get(self, *, upload_id: str) -> UploadRecord | None:
        """Fetch a single upload record, or ``None`` if it does not exist."""
        ...

    async def list_for_session(self, *, session_id: str) -> list[UploadRecord]:
        """List every upload recorded for ``session_id``."""
        ...


class InMemoryUploadRepository:
    """Process-local ``UploadRepository`` for local development and tests.

    Not suitable for production (state is not durable or shared across
    instances); production must configure a real backend.
    """

    def __init__(self) -> None:
        self._records: dict[str, UploadRecord] = {}
        self._lock = asyncio.Lock()

    async def put(self, record: UploadRecord) -> None:
        async with self._lock:
            self._records[record.id] = record

    async def get(self, *, upload_id: str) -> UploadRecord | None:
        async with self._lock:
            return self._records.get(upload_id)

    async def list_for_session(self, *, session_id: str) -> list[UploadRecord]:
        async with self._lock:
            return [
                record for record in self._records.values() if record.session_id == session_id
            ]
