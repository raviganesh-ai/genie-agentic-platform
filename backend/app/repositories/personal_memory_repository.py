"""Personal Agent Memory repository abstraction.

Isolated behind a protocol so the storage backend (in-memory for Phase 4;
Cosmos DB / Azure SQL per the Architecture Principles in
``.github/copilot-instructions.md`` for a later phase) can change without
touching ``PersonalMemoryStore`` or any caller.

Genie platform-change Phase G (gap-analysis follow-up): adds the Cosmos DB
implementation the module docstring above named as "a later phase" -
``ProductionSafetyValidator`` already requires
``settings.memory_store_backend == "cosmos_db"`` in production, but until
now there was no concrete class for Personal Agent Memory to actually use
that backend with - every personal-memory write was silently in-memory
regardless of that setting. Mirrors ``CosmosSharedMemoryRepository``
exactly (same partition-key/document-shape conventions).
"""
from __future__ import annotations

import asyncio
from typing import Protocol

from app.memory.memory_models import PersonalMemoryRecord
from app.repositories.document_store import DocumentStore

_PARTITION_PREFIX = "personal-memory"
_RECORD_TYPE = "personal-memory"
_METADATA_FIELDS = {
    "partitionKey",
    "recordType",
    "_rid",
    "_self",
    "_etag",
    "_attachments",
    "_ts",
}

__all__ = [
    "CosmosPersonalMemoryRepository",
    "InMemoryPersonalMemoryRepository",
    "PersonalMemoryRepository",
]


def _partition_key(session_id: str, agent_id: str) -> str:
    return f"{_PARTITION_PREFIX}:{session_id}:{agent_id}"


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


class CosmosPersonalMemoryRepository:
    """Durable Personal Agent Memory in the managed-identity Cosmos store.

    Mirrors ``CosmosSharedMemoryRepository`` exactly, except the partition
    key encodes both ``session_id`` AND ``agent_id`` (Personal Agent
    Memory's natural key is the three-part
    ``(session_id, agent_id, record.id)``, not just ``(session_id, id)``)
    so each agent's own memory stays genuinely isolated per the Memory
    Architecture's "accessible only by owning agent unless policy allows".
    """

    def __init__(self, *, store: DocumentStore) -> None:
        self._store = store

    async def put(self, record: PersonalMemoryRecord) -> None:
        document = record.model_dump(mode="json")
        document.update(
            {
                "partitionKey": _partition_key(record.session_id, record.agent_id),
                "recordType": _RECORD_TYPE,
            }
        )
        await self._store.upsert(document)

    async def get(
        self, *, session_id: str, agent_id: str, record_id: str
    ) -> PersonalMemoryRecord | None:
        document = await self._store.read(
            document_id=record_id,
            partition_key=_partition_key(session_id, agent_id),
        )
        if document is None:
            return None
        return self._to_model(document)

    async def list_for_agent(
        self, *, session_id: str, agent_id: str
    ) -> list[PersonalMemoryRecord]:
        documents = await self._store.query(
            query="SELECT * FROM c WHERE c.recordType = @recordType",
            parameters=[{"name": "@recordType", "value": _RECORD_TYPE}],
            partition_key=_partition_key(session_id, agent_id),
        )
        return [self._to_model(document) for document in documents]

    @staticmethod
    def _to_model(document: dict[str, object]) -> PersonalMemoryRecord:
        return PersonalMemoryRecord.model_validate(
            {key: value for key, value in document.items() if key not in _METADATA_FIELDS}
        )
