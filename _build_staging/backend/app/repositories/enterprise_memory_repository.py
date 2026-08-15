"""Enterprise Knowledge Memory repository abstraction.

Prepared for a future Azure AI Search-backed implementation (see the
Memory Architecture / Architecture Principles in
``.github/copilot-instructions.md``): every method mirrors an operation
Azure AI Search naturally supports (upsert a document, fetch by id,
query/search). Azure AI Search integration itself is explicitly out of
scope for Phase 4 - only this interface and an in-memory reference
implementation are provided; swapping in a real
``azure-search-documents``-backed implementation later requires no change
to ``EnterpriseKnowledgeStore`` or any caller.
"""
from __future__ import annotations

import asyncio
from typing import Protocol

from app.memory.memory_models import EnterpriseKnowledgeRecord


class EnterpriseMemoryRepository(Protocol):
    """Cross-session storage for Enterprise Knowledge Memory."""

    async def upsert(self, record: EnterpriseKnowledgeRecord) -> None:
        """Insert or replace a record by its id."""
        ...

    async def get(self, *, record_id: str) -> EnterpriseKnowledgeRecord | None:
        """Fetch a single record, or ``None`` if it does not exist."""
        ...

    async def search(self, *, query: str, top: int = 10) -> list[EnterpriseKnowledgeRecord]:
        """Return up to ``top`` records relevant to ``query``."""
        ...


class InMemoryEnterpriseKnowledgeRepository:
    """Process-local ``EnterpriseMemoryRepository`` for local development and tests.

    ``search`` performs a naive case-insensitive substring match over each
    record's ``content`` values - a placeholder for the semantic/vector
    search Azure AI Search will provide. Not suitable for production (state
    is not durable, shared across instances, or reviewer-governed).
    """

    def __init__(self) -> None:
        self._records: dict[str, EnterpriseKnowledgeRecord] = {}
        self._lock = asyncio.Lock()

    async def upsert(self, record: EnterpriseKnowledgeRecord) -> None:
        async with self._lock:
            self._records[record.id] = record

    async def get(self, *, record_id: str) -> EnterpriseKnowledgeRecord | None:
        async with self._lock:
            return self._records.get(record_id)

    async def search(self, *, query: str, top: int = 10) -> list[EnterpriseKnowledgeRecord]:
        async with self._lock:
            needle = query.strip().lower()
            matches = [
                record
                for record in self._records.values()
                if not needle
                or any(needle in str(value).lower() for value in record.content.values())
            ]
            return matches[:top]
