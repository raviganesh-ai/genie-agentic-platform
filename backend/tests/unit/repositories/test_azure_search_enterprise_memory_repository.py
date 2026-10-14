"""Unit tests for AzureSearchEnterpriseKnowledgeRepository (Genie platform-change
Phase G: Enterprise Knowledge Memory's real Azure AI Search backend).

No live Azure AI Search resource is available in this environment, so
these tests use a fake SearchClient test double whose method shapes were
verified against the real, installed azure-search-documents SDK before
writing either the production code or this test (see
app.repositories.enterprise_memory_repository's own docstring) - the
REPOSITORY'S OWN to-document/to-model mapping logic under test is never
itself mocked or faked.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from azure.core.exceptions import ResourceNotFoundError

from app.memory.memory_models import EnterpriseKnowledgeRecord, MemoryLineage
from app.repositories.enterprise_memory_repository import (
    AzureSearchEnterpriseKnowledgeRepository,
    EnterpriseMemorySearchError,
)


class _FakeIndexingResult:
    def __init__(self, key: str, *, succeeded: bool, error_message: str | None = None) -> None:
        self.key = key
        self.succeeded = succeeded
        self.error_message = error_message


class _FakeAsyncSearchResults:
    """Minimal async-iterable matching AsyncSearchItemPaged's own real
    `async for result in results:` usage contract - never the thing under
    test, only a stand-in for Azure Search's own paging machinery."""

    def __init__(self, items: list[dict]) -> None:
        self._items = items

    def __aiter__(self):
        return self._generator()

    async def _generator(self):
        for item in self._items:
            yield item


class _FakeSearchClient:
    """Fake double for azure.search.documents.aio.SearchClient, matching the
    real, verified async method signatures (upload_documents/get_document/
    search). Documents are stored exactly as the repository under test
    would really send/receive them - this fake never fabricates the
    repository's own to-document/to-model translation."""

    def __init__(self, *, reject_ids: frozenset[str] = frozenset()) -> None:
        self.documents: dict[str, dict] = {}
        self._reject_ids = reject_ids

    async def upload_documents(self, documents: list[dict]) -> list[_FakeIndexingResult]:
        results = []
        for document in documents:
            if document["id"] in self._reject_ids:
                results.append(
                    _FakeIndexingResult(
                        key=document["id"], succeeded=False, error_message="Simulated rejection."
                    )
                )
                continue
            self.documents[document["id"]] = document
            results.append(_FakeIndexingResult(key=document["id"], succeeded=True))
        return results

    async def get_document(self, *, key: str) -> dict:
        if key not in self.documents:
            raise ResourceNotFoundError(f"Document '{key}' not found.")
        return self.documents[key]

    async def search(self, *, search_text: str, top: int | None = None) -> _FakeAsyncSearchResults:
        if search_text == "*":
            matches = list(self.documents.values())
        else:
            needle = search_text.lower()
            matches = [
                document
                for document in self.documents.values()
                if needle in document["searchable_text"].lower()
            ]
        return _FakeAsyncSearchResults(matches[: top or len(matches)])


def _record(record_id: str = "pattern-1") -> EnterpriseKnowledgeRecord:
    return EnterpriseKnowledgeRecord(
        id=record_id,
        classification="industry_pattern",
        content={"summary": "Event-driven claims intake reference architecture"},
        lineage=MemoryLineage(
            session_id="session-1",
            trace_id="trace-1",
            agent_id="architecture-designer",
            agent_version="1.0.0",
            timestamp=datetime.now(UTC),
            confidence_score=0.9,
            approval_status="approved",
        ),
    )


async def test_upsert_and_get_round_trip_through_the_real_document_mapping():
    search_client = _FakeSearchClient()
    repository = AzureSearchEnterpriseKnowledgeRepository(search_client=search_client)
    record = _record()

    await repository.upsert(record)
    result = await repository.get(record_id="pattern-1")

    assert result == record


async def test_get_returns_none_for_an_unknown_record():
    repository = AzureSearchEnterpriseKnowledgeRepository(search_client=_FakeSearchClient())

    assert await repository.get(record_id="unknown") is None


async def test_upsert_raises_when_azure_search_rejects_the_document():
    search_client = _FakeSearchClient(reject_ids=frozenset({"pattern-1"}))
    repository = AzureSearchEnterpriseKnowledgeRepository(search_client=search_client)

    with pytest.raises(EnterpriseMemorySearchError, match="Simulated rejection"):
        await repository.upsert(_record())


async def test_search_matches_against_the_real_searchable_text_projection():
    search_client = _FakeSearchClient()
    repository = AzureSearchEnterpriseKnowledgeRepository(search_client=search_client)
    await repository.upsert(_record("pattern-1"))
    await repository.upsert(
        EnterpriseKnowledgeRecord(
            id="pattern-2",
            classification="reference_architecture",
            content={"summary": "Batch ETL pipeline reference architecture"},
            lineage=MemoryLineage(
                session_id="session-1",
                trace_id="trace-1",
                agent_id="architecture-designer",
                agent_version="1.0.0",
                timestamp=datetime.now(UTC),
                confidence_score=0.9,
            ),
        )
    )

    results = await repository.search(query="claims intake")

    assert [record.id for record in results] == ["pattern-1"]


async def test_search_with_empty_query_matches_everything():
    search_client = _FakeSearchClient()
    repository = AzureSearchEnterpriseKnowledgeRepository(search_client=search_client)
    await repository.upsert(_record("pattern-1"))

    results = await repository.search(query="")

    assert [record.id for record in results] == ["pattern-1"]
