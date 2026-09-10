"""Session repository abstraction.

Isolated behind a protocol so the storage backend (in-memory for local
development and tests; Cosmos DB / Azure SQL for a later phase) can change
without touching ``SessionService`` or any caller - mirroring every other
repository in ``app.repositories``.
"""
from __future__ import annotations

import asyncio
from typing import Protocol

from app.models.session_models import Session
from app.repositories.document_store import DocumentStore

_PARTITION_KEY = "sessions"
_METADATA_FIELDS = {"partitionKey", "recordType", "_rid", "_self", "_etag", "_attachments", "_ts"}


class SessionRepository(Protocol):
    """Storage for ``Session`` records."""

    async def put(self, session: Session) -> None:
        """Insert or replace a session by its id."""
        ...

    async def get(self, *, session_id: str) -> Session | None:
        """Fetch a single session, or ``None`` if it does not exist."""
        ...

    async def list_for_owner(self, *, owner_user_id: str) -> list[Session]:
        """List every session owned by ``owner_user_id``."""
        ...


class InMemorySessionRepository:
    """Process-local ``SessionRepository`` for local development and tests.

    Not suitable for production (state is not durable or shared across
    instances); production must configure a real backend (Cosmos DB /
    Azure SQL per the Architecture Principles in
    ``.github/copilot-instructions.md``).
    """

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()

    async def put(self, session: Session) -> None:
        async with self._lock:
            self._sessions[session.id] = session

    async def get(self, *, session_id: str) -> Session | None:
        async with self._lock:
            return self._sessions.get(session_id)

    async def list_for_owner(self, *, owner_user_id: str) -> list[Session]:
        async with self._lock:
            return [
                session
                for session in self._sessions.values()
                if session.owner_user_id == owner_user_id
            ]


class CosmosSessionRepository:
    """Durable session storage in Genie's managed-identity Cosmos container."""

    def __init__(self, *, store: DocumentStore) -> None:
        self._store = store

    async def put(self, session: Session) -> None:
        document = session.model_dump(mode="json")
        document.update(
            {
                "id": session.id,
                "partitionKey": _PARTITION_KEY,
                "recordType": "session",
            }
        )
        await self._store.upsert(document)

    async def get(self, *, session_id: str) -> Session | None:
        document = await self._store.read(
            document_id=session_id,
            partition_key=_PARTITION_KEY,
        )
        if document is None:
            return None
        return Session.model_validate(
            {key: value for key, value in document.items() if key not in _METADATA_FIELDS}
        )

    async def list_for_owner(self, *, owner_user_id: str) -> list[Session]:
        documents = await self._store.query(
            query=(
                "SELECT * FROM c WHERE c.recordType = @recordType "
                "AND c.owner_user_id = @ownerUserId ORDER BY c.created_at"
            ),
            parameters=[
                {"name": "@recordType", "value": "session"},
                {"name": "@ownerUserId", "value": owner_user_id},
            ],
            partition_key=_PARTITION_KEY,
        )
        return [
            Session.model_validate(
                {key: value for key, value in document.items() if key not in _METADATA_FIELDS}
            )
            for document in documents
        ]
