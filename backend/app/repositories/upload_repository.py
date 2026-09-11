"""Upload record repository abstraction.

Isolated behind a protocol so the storage backend (in-memory for local
development and tests; a durable backend for a later phase) can change
without touching ``SessionService`` or any caller.
"""
from __future__ import annotations

import asyncio
from typing import Protocol

from app.models.upload_models import UploadRecord
from app.repositories.document_store import DocumentStore

__all__ = ["CosmosUploadRepository", "InMemoryUploadRepository", "UploadRepository"]

_PARTITION_KEY = "uploads"
_METADATA_FIELDS = {"partitionKey", "recordType", "_rid", "_self", "_etag", "_attachments", "_ts"}


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


class CosmosUploadRepository:
    """Durable upload metadata and extracted text in Genie's Cosmos container."""

    def __init__(self, *, store: DocumentStore) -> None:
        self._store = store

    async def put(self, record: UploadRecord) -> None:
        document = record.model_dump(mode="json")
        document.update(
            {
                "partitionKey": _PARTITION_KEY,
                "recordType": "upload",
            }
        )
        await self._store.upsert(document)

    async def get(self, *, upload_id: str) -> UploadRecord | None:
        document = await self._store.read(
            document_id=upload_id,
            partition_key=_PARTITION_KEY,
        )
        if document is None:
            return None
        return self._to_model(document)

    async def list_for_session(self, *, session_id: str) -> list[UploadRecord]:
        documents = await self._store.query(
            query=(
                "SELECT * FROM c WHERE c.recordType = @recordType "
                "AND c.session_id = @sessionId"
            ),
            parameters=[
                {"name": "@recordType", "value": "upload"},
                {"name": "@sessionId", "value": session_id},
            ],
            partition_key=_PARTITION_KEY,
        )
        return [self._to_model(document) for document in documents]

    @staticmethod
    def _to_model(document: dict[str, object]) -> UploadRecord:
        return UploadRecord.model_validate(
            {key: value for key, value in document.items() if key not in _METADATA_FIELDS}
        )
