"""Lessons-learned repository abstraction (Genie platform-change Phase F).

Mirrors ``app.repositories.workflow_run_repository`` exactly: isolated
behind a protocol so the storage backend (in-memory for local development
and tests; Cosmos DB for production, per the Architecture Principles in
``.github/copilot-instructions.md``) can change without touching
``DebuggingWorkflowService`` or any caller.
"""
from __future__ import annotations

import asyncio
from typing import Protocol

from app.models.lessons_learned import LessonLearned
from app.repositories.document_store import DocumentStore

__all__ = [
    "CosmosLessonsLearnedRepository",
    "InMemoryLessonsLearnedRepository",
    "LessonsLearnedRepository",
]

_PARTITION_KEY = "lessons-learned"
_METADATA_FIELDS = {
    "id",
    "partitionKey",
    "recordType",
    "_rid",
    "_self",
    "_etag",
    "_attachments",
    "_ts",
}


class LessonsLearnedRepository(Protocol):
    """Storage for ``LessonLearned`` records (one entry per diagnosed root cause)."""

    async def put(self, lesson: LessonLearned) -> None:
        """Insert or replace one lesson-learned record."""
        ...

    async def list_open(self) -> list[LessonLearned]:
        """List every lesson still awaiting human review, oldest first."""
        ...


class InMemoryLessonsLearnedRepository:
    """Process-local ``LessonsLearnedRepository`` for local development and tests.

    Not suitable for production (state is not durable or shared across
    instances); production must configure a real backend (Cosmos DB per
    the Architecture Principles in ``.github/copilot-instructions.md``).
    """

    def __init__(self) -> None:
        self._lessons: dict[str, LessonLearned] = {}
        self._order: list[str] = []
        self._lock = asyncio.Lock()

    async def put(self, lesson: LessonLearned) -> None:
        async with self._lock:
            if lesson.id not in self._lessons:
                self._order.append(lesson.id)
            self._lessons[lesson.id] = lesson

    async def list_open(self) -> list[LessonLearned]:
        async with self._lock:
            return [
                self._lessons[lesson_id]
                for lesson_id in self._order
                if self._lessons[lesson_id].status == "open"
            ]


class CosmosLessonsLearnedRepository:
    """Durable lessons-learned records in Genie's managed-identity Cosmos container."""

    def __init__(self, *, store: DocumentStore) -> None:
        self._store = store

    async def put(self, lesson: LessonLearned) -> None:
        document = lesson.model_dump(mode="json")
        document.update(
            {
                "id": lesson.id,
                "partitionKey": _PARTITION_KEY,
                "recordType": "lesson-learned",
            }
        )
        await self._store.upsert(document)

    async def list_open(self) -> list[LessonLearned]:
        documents = await self._store.query(
            query=(
                "SELECT * FROM c WHERE c.recordType = @recordType AND c.status = @status"
            ),
            parameters=[
                {"name": "@recordType", "value": "lesson-learned"},
                {"name": "@status", "value": "open"},
            ],
            partition_key=_PARTITION_KEY,
        )
        return [self._to_model(document) for document in documents]

    @staticmethod
    def _to_model(document: dict[str, object]) -> LessonLearned:
        return LessonLearned.model_validate(
            {key: value for key, value in document.items() if key not in _METADATA_FIELDS}
        )
