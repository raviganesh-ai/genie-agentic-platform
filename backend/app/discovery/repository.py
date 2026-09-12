"""Persistence abstractions for durable Discovery cases."""
from __future__ import annotations

import asyncio
from typing import Protocol

from app.discovery.models import DiscoveryCase
from app.repositories.document_store import DocumentStore

__all__ = [
    "CosmosDiscoveryCaseRepository",
    "DiscoveryCaseRepository",
    "InMemoryDiscoveryCaseRepository",
]

_PARTITION_KEY = "discovery-cases"
_METADATA_FIELDS = {"partitionKey", "recordType", "_rid", "_self", "_etag", "_attachments", "_ts"}


class DiscoveryCaseRepository(Protocol):
    async def put(self, discovery_case: DiscoveryCase) -> None: ...

    async def get(self, *, discovery_case_id: str) -> DiscoveryCase | None: ...

    async def get_for_session(self, *, session_id: str) -> DiscoveryCase | None: ...

    async def list_for_owner(self, *, owner_user_id: str) -> list[DiscoveryCase]: ...

    async def delete(self, *, discovery_case_id: str) -> None: ...


class InMemoryDiscoveryCaseRepository:
    """Process-local repository for tests and local development."""

    def __init__(self) -> None:
        self._cases: dict[str, DiscoveryCase] = {}
        self._lock = asyncio.Lock()

    async def put(self, discovery_case: DiscoveryCase) -> None:
        async with self._lock:
            self._cases[discovery_case.id] = discovery_case.model_copy(deep=True)

    async def get(self, *, discovery_case_id: str) -> DiscoveryCase | None:
        async with self._lock:
            discovery_case = self._cases.get(discovery_case_id)
            return discovery_case.model_copy(deep=True) if discovery_case is not None else None

    async def get_for_session(self, *, session_id: str) -> DiscoveryCase | None:
        async with self._lock:
            discovery_case = next(
                (item for item in self._cases.values() if item.session_id == session_id),
                None,
            )
            return discovery_case.model_copy(deep=True) if discovery_case is not None else None

    async def list_for_owner(self, *, owner_user_id: str) -> list[DiscoveryCase]:
        async with self._lock:
            return [
                discovery_case.model_copy(deep=True)
                for discovery_case in self._cases.values()
                if discovery_case.owner_user_id == owner_user_id
            ]

    async def delete(self, *, discovery_case_id: str) -> None:
        async with self._lock:
            self._cases.pop(discovery_case_id, None)


class CosmosDiscoveryCaseRepository:
    """Durable Discovery storage in Genie's managed-identity Cosmos container."""

    def __init__(self, *, store: DocumentStore) -> None:
        self._store = store

    async def put(self, discovery_case: DiscoveryCase) -> None:
        document = discovery_case.model_dump(mode="json")
        document.update(
            {
                "id": discovery_case.id,
                "partitionKey": _PARTITION_KEY,
                "recordType": "discovery-case",
            }
        )
        await self._store.upsert(document)

    async def get(self, *, discovery_case_id: str) -> DiscoveryCase | None:
        document = await self._store.read(
            document_id=discovery_case_id,
            partition_key=_PARTITION_KEY,
        )
        if document is None:
            return None
        return DiscoveryCase.model_validate(
            {key: value for key, value in document.items() if key not in _METADATA_FIELDS}
        )

    async def get_for_session(self, *, session_id: str) -> DiscoveryCase | None:
        documents = await self._store.query(
            query=(
                "SELECT * FROM c WHERE c.recordType = @recordType "
                "AND c.session_id = @sessionId"
            ),
            parameters=[
                {"name": "@recordType", "value": "discovery-case"},
                {"name": "@sessionId", "value": session_id},
            ],
            partition_key=_PARTITION_KEY,
        )
        if not documents:
            return None
        return DiscoveryCase.model_validate(
            {key: value for key, value in documents[0].items() if key not in _METADATA_FIELDS}
        )

    async def list_for_owner(self, *, owner_user_id: str) -> list[DiscoveryCase]:
        documents = await self._store.query(
            query=(
                "SELECT * FROM c WHERE c.recordType = @recordType "
                "AND c.owner_user_id = @ownerUserId ORDER BY c.updated_at DESC"
            ),
            parameters=[
                {"name": "@recordType", "value": "discovery-case"},
                {"name": "@ownerUserId", "value": owner_user_id},
            ],
            partition_key=_PARTITION_KEY,
        )
        return [
            DiscoveryCase.model_validate(
                {key: value for key, value in document.items() if key not in _METADATA_FIELDS}
            )
            for document in documents
        ]

    async def delete(self, *, discovery_case_id: str) -> None:
        await self._store.delete(
            document_id=discovery_case_id,
            partition_key=_PARTITION_KEY,
        )