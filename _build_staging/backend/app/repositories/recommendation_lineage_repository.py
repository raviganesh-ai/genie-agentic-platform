"""Recommendation lineage repository abstraction.

Isolated behind a protocol so the storage backend (in-memory for Phase 5;
Cosmos DB / Azure SQL for a later phase, per ``Settings.lineage_store_backend``)
can change without touching ``RecommendationLineageService`` or any caller.
"""
from __future__ import annotations

import asyncio
from typing import Protocol

from app.models.recommendation_lineage import RecommendationLineage


class RecommendationLineageRepository(Protocol):
    """Session-scoped storage for recommendation lineage records."""

    async def put(self, lineage: RecommendationLineage) -> None:
        """Insert or replace a record by its id."""
        ...

    async def get(
        self, *, session_id: str, recommendation_id: str
    ) -> RecommendationLineage | None:
        """Fetch the lineage for a single recommendation, if any."""
        ...

    async def list_for_session(self, *, session_id: str) -> list[RecommendationLineage]:
        """List every recommendation lineage record within ``session_id``."""
        ...


class InMemoryRecommendationLineageRepository:
    """Process-local ``RecommendationLineageRepository`` for local development and tests.

    Not suitable for production (state is not durable or shared across
    instances); production must configure a real backend (see
    ``Settings.lineage_store_backend``).
    """

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], RecommendationLineage] = {}
        self._lock = asyncio.Lock()

    async def put(self, lineage: RecommendationLineage) -> None:
        async with self._lock:
            self._records[(lineage.session_id, lineage.recommendation_id)] = lineage

    async def get(
        self, *, session_id: str, recommendation_id: str
    ) -> RecommendationLineage | None:
        async with self._lock:
            return self._records.get((session_id, recommendation_id))

    async def list_for_session(self, *, session_id: str) -> list[RecommendationLineage]:
        async with self._lock:
            return [
                record
                for (rec_session_id, _), record in self._records.items()
                if rec_session_id == session_id
            ]
