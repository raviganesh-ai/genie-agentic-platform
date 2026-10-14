"""Enterprise Knowledge Memory repository abstraction.

Prepared for a future Azure AI Search-backed implementation (see the
Memory Architecture / Architecture Principles in
``.github/copilot-instructions.md``): every method mirrors an operation
Azure AI Search naturally supports (upsert a document, fetch by id,
query/search). Azure AI Search integration itself was explicitly out of
scope for Phase 4 - only this interface and an in-memory reference
implementation were provided there; swapping in a real
``azure-search-documents``-backed implementation later requires no change
to ``EnterpriseKnowledgeStore`` or any caller.

Genie platform-change Phase G (gap-analysis follow-up): adds that real
``azure-search-documents``-backed implementation
(``AzureSearchEnterpriseKnowledgeRepository``) - until now, Enterprise
Knowledge Memory's real backend did not exist at all, so every promoted
industry pattern/reference architecture was silently in-memory
regardless of environment, and its "search" was a naive substring match
rather than the real full-text search the Memory Architecture requires.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from app.memory.memory_models import EnterpriseKnowledgeRecord, MemoryLineage

if TYPE_CHECKING:
    from azure.search.documents.aio import SearchClient

__all__ = [
    "AzureSearchEnterpriseKnowledgeRepository",
    "EnterpriseMemoryRepository",
    "EnterpriseMemorySearchError",
    "InMemoryEnterpriseKnowledgeRepository",
]


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


class EnterpriseMemorySearchError(RuntimeError):
    """Raised when Azure AI Search rejects an upsert or a lookup fails unexpectedly."""


class AzureSearchEnterpriseKnowledgeRepository:
    """Durable, full-text-searchable Enterprise Knowledge Memory backed by a
    real Azure AI Search index (``azure-search-documents``'s async
    ``SearchClient`` - real method signatures verified against the
    installed SDK before writing this class: ``upload_documents(documents)``
    returns a list of ``IndexingResult`` with ``.key``/``.succeeded``/
    ``.error_message``; ``get_document(key=...)`` raises
    ``azure.core.exceptions.ResourceNotFoundError`` when the key does not
    exist, it does not return ``None``; ``search(search_text=..., top=...)``
    returns an ``AsyncSearchItemPaged`` that must be iterated with
    ``async for``).

    Assumes the Azure AI Search index named by ``search_client``'s own
    ``index_name`` already exists with this exact field schema (index
    provisioning is an infrastructure concern, the same assumption
    ``BackendDeploymentService`` already makes about its ACR registry /
    Container Apps environment already existing):

    - ``id`` (Edm.String, key) - must already be a valid Azure Search
      document key (letters, digits, ``_``, ``-``, ``=``); this class does
      not sanitize ``EnterpriseKnowledgeRecord.id``, it is the caller's
      responsibility to supply a conforming id.
    - ``classification`` (Edm.String, filterable)
    - ``content_json`` (Edm.String) - the record's full, open-ended
      ``content`` dict, JSON-serialized verbatim (Azure Search fields are
      flat/typed; this preserves arbitrary externally supplied content
      exactly rather than guessing a schema for it).
    - ``searchable_text`` (Edm.String, searchable) - every string/number
      value found in ``content``, concatenated, so a real full-text query
      can actually match the record's substance.
    - ``lineage_session_id``, ``lineage_trace_id``, ``lineage_agent_id``,
      ``lineage_agent_version``, ``lineage_timestamp`` (Edm.String,
      ``lineage_timestamp`` is ISO 8601), ``lineage_approval_status``
      (Edm.String)
    - ``lineage_evidence_references`` (Collection(Edm.String))
    - ``lineage_confidence_score`` (Edm.Double)
    """

    def __init__(self, *, search_client: SearchClient) -> None:
        self._search_client = search_client

    async def upsert(self, record: EnterpriseKnowledgeRecord) -> None:
        document = self._to_document(record)
        results = await self._search_client.upload_documents(documents=[document])
        failed = [result for result in results if not result.succeeded]
        if failed:
            raise EnterpriseMemorySearchError(
                f"Azure AI Search rejected Enterprise Knowledge record "
                f"'{record.id}': {failed[0].error_message}"
            )

    async def get(self, *, record_id: str) -> EnterpriseKnowledgeRecord | None:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            document = await self._search_client.get_document(key=record_id)
        except ResourceNotFoundError:
            return None
        return self._to_model(document)

    async def search(self, *, query: str, top: int = 10) -> list[EnterpriseKnowledgeRecord]:
        # Azure AI Search's own convention: an empty/absent search_text
        # does not mean "match nothing" - "*" is its documented match-all
        # query, mirrored here so an empty query behaves the same as
        # InMemoryEnterpriseKnowledgeRepository's own "no needle matches
        # everything" rule, not silently returning zero results.
        results = await self._search_client.search(search_text=query.strip() or "*", top=top)
        records: list[EnterpriseKnowledgeRecord] = []
        async for result in results:
            records.append(self._to_model(result))
        return records

    @staticmethod
    def _to_document(record: EnterpriseKnowledgeRecord) -> dict[str, object]:
        searchable_values = [
            value for value in record.content.values() if isinstance(value, str | int | float)
        ]
        return {
            "id": record.id,
            "classification": record.classification,
            "content_json": json.dumps(record.content),
            "searchable_text": " ".join(str(value) for value in searchable_values),
            "lineage_session_id": record.lineage.session_id,
            "lineage_trace_id": record.lineage.trace_id,
            "lineage_agent_id": record.lineage.agent_id,
            "lineage_agent_version": record.lineage.agent_version,
            "lineage_timestamp": record.lineage.timestamp.isoformat(),
            "lineage_evidence_references": list(record.lineage.evidence_references),
            "lineage_confidence_score": record.lineage.confidence_score,
            "lineage_approval_status": record.lineage.approval_status,
        }

    @staticmethod
    def _to_model(document: dict[str, object]) -> EnterpriseKnowledgeRecord:
        return EnterpriseKnowledgeRecord(
            id=document["id"],  # type: ignore[arg-type]
            classification=document["classification"],  # type: ignore[arg-type]
            content=json.loads(document["content_json"]),  # type: ignore[arg-type]
            lineage=MemoryLineage(
                session_id=document["lineage_session_id"],  # type: ignore[arg-type]
                trace_id=document["lineage_trace_id"],  # type: ignore[arg-type]
                agent_id=document["lineage_agent_id"],  # type: ignore[arg-type]
                agent_version=document["lineage_agent_version"],  # type: ignore[arg-type]
                timestamp=datetime.fromisoformat(document["lineage_timestamp"]),  # type: ignore[arg-type]
                evidence_references=list(document.get("lineage_evidence_references") or []),  # type: ignore[arg-type]
                confidence_score=document["lineage_confidence_score"],  # type: ignore[arg-type]
                approval_status=document["lineage_approval_status"],  # type: ignore[arg-type]
            ),
        )

