"""Shared Collaboration Memory repository abstraction.

Isolated behind a protocol so the storage backend (in-memory for Phase 4;
Cosmos DB / Azure SQL for a later phase) can change without touching
``SharedMemoryStore`` or any caller.
"""
from __future__ import annotations

import asyncio
from typing import Protocol

from app.memory.memory_models import SharedMemoryRecord
from app.repositories.document_store import DocumentStore

__all__ = [
    "CosmosSharedMemoryRepository",
    "InMemorySharedMemoryRepository",
    "SharedMemoryRepository",
]

_PARTITION_PREFIX = "shared-memory"
_METADATA_FIELDS = {
    "partitionKey",
    "recordType",
    "_rid",
    "_self",
    "_etag",
    "_attachments",
    "_ts",
}


def _partition_key(session_id: str) -> str:
    return f"{_PARTITION_PREFIX}:{session_id}"


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


class CosmosSharedMemoryRepository:
    """Durable Shared Collaboration Memory in the managed-identity Cosmos store."""

    def __init__(self, *, store: DocumentStore) -> None:
        self._store = store

    async def get(self, *, session_id: str, key: str) -> SharedMemoryRecord | None:
        document = await self._store.read(
            document_id=key,
            partition_key=_partition_key(session_id),
        )
        if document is None:
            return None
        return self._to_model(document)

    async def put(self, record: SharedMemoryRecord) -> None:
        document = record.model_dump(mode="json")
        document.update(
            {
                "partitionKey": _partition_key(record.session_id),
                "recordType": "shared-memory",
            }
        )
        await self._store.upsert(document)

    async def list_for_session(self, *, session_id: str) -> list[SharedMemoryRecord]:
        documents = await self._store.query(
            query="SELECT * FROM c WHERE c.recordType = @recordType",
            parameters=[{"name": "@recordType", "value": "shared-memory"}],
            partition_key=_partition_key(session_id),
        )
        return [self._to_model(document) for document in documents]

    async def delete_prefix(self, *, session_id: str, key_prefix: str) -> int:
        records = await self.list_for_session(session_id=session_id)
        matching_ids = [record.id for record in records if record.id.startswith(key_prefix)]
        for record_id in matching_ids:
            await self._store.delete(
                document_id=record_id,
                partition_key=_partition_key(session_id),
            )
        return len(matching_ids)

    @staticmethod
    def _to_model(document: dict[str, object]) -> SharedMemoryRecord:
        return SharedMemoryRecord.model_validate(
            {key: value for key, value in document.items() if key not in _METADATA_FIELDS}
        )
