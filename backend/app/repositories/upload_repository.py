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
# JSON may encode one non-BMP character as a 12-byte surrogate pair. This
# bound leaves ample room below Cosmos DB's 2 MiB item limit in that worst case.
_TRANSCRIPT_CHUNK_CHARACTERS = 125_000
_TRANSCRIPT_CHUNK_COUNT_FIELD = "transcriptChunkCount"
_TRANSCRIPT_CHUNK_RECORD_TYPE = "uploadTranscriptChunk"
_METADATA_FIELDS = {
    "partitionKey",
    "recordType",
    _TRANSCRIPT_CHUNK_COUNT_FIELD,
    "_rid",
    "_self",
    "_etag",
    "_attachments",
    "_ts",
}


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

    async def delete(self, *, upload_id: str) -> None:
        """Delete an upload and any storage records owned by it."""
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

    async def delete(self, *, upload_id: str) -> None:
        async with self._lock:
            self._records.pop(upload_id, None)


class CosmosUploadRepository:
    """Durable upload metadata and extracted text in Genie's Cosmos container."""

    def __init__(self, *, store: DocumentStore) -> None:
        self._store = store

    async def put(self, record: UploadRecord) -> None:
        document = record.model_dump(mode="json")
        transcript_text = document.pop("transcript_text", None)
        transcript_chunks = self._split_transcript(transcript_text)
        chunk_transcript = len(transcript_chunks) > 1
        document.update(
            {
                "partitionKey": _PARTITION_KEY,
                "recordType": "upload",
                "transcript_text": None if chunk_transcript else transcript_text,
                _TRANSCRIPT_CHUNK_COUNT_FIELD: len(transcript_chunks) if chunk_transcript else 0,
            }
        )
        if chunk_transcript:
            for index, text in enumerate(transcript_chunks):
                await self._store.upsert(
                    {
                        "id": self._chunk_id(record.id, index),
                        "partitionKey": _PARTITION_KEY,
                        "recordType": _TRANSCRIPT_CHUNK_RECORD_TYPE,
                        "session_id": record.session_id,
                        "upload_id": record.id,
                        "chunk_index": index,
                        "text": text,
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
        return await self._to_model(document)

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
        return list(await asyncio.gather(*(self._to_model(document) for document in documents)))

    async def delete(self, *, upload_id: str) -> None:
        document = await self._store.read(
            document_id=upload_id,
            partition_key=_PARTITION_KEY,
        )
        if document is None:
            return
        chunk_count = int(document.get(_TRANSCRIPT_CHUNK_COUNT_FIELD, 0))
        await asyncio.gather(
            *(
                self._store.delete(
                    document_id=self._chunk_id(upload_id, index),
                    partition_key=_PARTITION_KEY,
                )
                for index in range(chunk_count)
            )
        )
        await self._store.delete(document_id=upload_id, partition_key=_PARTITION_KEY)

    async def _to_model(self, document: dict[str, object]) -> UploadRecord:
        model_data = {
            key: value for key, value in document.items() if key not in _METADATA_FIELDS
        }
        chunk_count = int(document.get(_TRANSCRIPT_CHUNK_COUNT_FIELD, 0))
        if chunk_count:
            chunks = await asyncio.gather(
                *(
                    self._store.read(
                        document_id=self._chunk_id(str(document["id"]), index),
                        partition_key=_PARTITION_KEY,
                    )
                    for index in range(chunk_count)
                )
            )
            if any(chunk is None for chunk in chunks):
                raise RuntimeError(
                    f"Upload '{document['id']}' has incomplete transcript storage."
                )
            model_data["transcript_text"] = "".join(str(chunk["text"]) for chunk in chunks if chunk)
        return UploadRecord.model_validate(model_data)

    @staticmethod
    def _chunk_id(upload_id: str, index: int) -> str:
        return f"{upload_id}:transcript:{index}"

    @staticmethod
    def _split_transcript(transcript_text: object) -> list[str]:
        if not isinstance(transcript_text, str) or not transcript_text:
            return []
        return [
            transcript_text[index : index + _TRANSCRIPT_CHUNK_CHARACTERS]
            for index in range(0, len(transcript_text), _TRANSCRIPT_CHUNK_CHARACTERS)
        ]
