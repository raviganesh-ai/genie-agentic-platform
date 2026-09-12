"""Application service for durable, user-owned Discovery cases."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.discovery.models import DiscoveryCase
from app.discovery.repository import DiscoveryCaseRepository, InMemoryDiscoveryCaseRepository
from app.services.session_service import SessionAccessDeniedError, SessionService

__all__ = [
    "DiscoveryCaseNotFoundError",
    "DiscoveryService",
    "create_discovery_service",
]


class DiscoveryCaseNotFoundError(RuntimeError):
    """Raised when a session has no durable Discovery case."""


class DiscoveryService:
    def __init__(
        self,
        *,
        session_service: SessionService,
        repository: DiscoveryCaseRepository,
    ) -> None:
        self._session_service = session_service
        self._repository = repository

    async def create_or_resume(
        self,
        *,
        session_id: str,
        requesting_user_id: str,
        source_upload_ids: list[str],
    ) -> DiscoveryCase:
        await self._session_service.get_session(
            session_id=session_id,
            requesting_user_id=requesting_user_id,
        )
        unique_upload_ids = list(dict.fromkeys(source_upload_ids))
        for upload_id in unique_upload_ids:
            await self._session_service.get_upload(
                session_id=session_id,
                upload_id=upload_id,
                requesting_user_id=requesting_user_id,
            )

        existing = await self._repository.get_for_session(session_id=session_id)
        if existing is not None:
            if existing.owner_user_id != requesting_user_id:
                raise SessionAccessDeniedError(
                    f"User '{requesting_user_id}' is not authorized to access "
                    f"Discovery case '{existing.id}'."
                )
            merged_upload_ids = list(
                dict.fromkeys([*existing.source_upload_ids, *unique_upload_ids])
            )
            if merged_upload_ids == existing.source_upload_ids:
                return existing
            updated = existing.model_copy(
                update={
                    "source_upload_ids": merged_upload_ids,
                    "version": existing.version + 1,
                    "updated_at": datetime.now(UTC),
                }
            )
            await self._repository.put(updated)
            return updated

        now = datetime.now(UTC)
        discovery_case = DiscoveryCase(
            id=str(uuid4()),
            session_id=session_id,
            owner_user_id=requesting_user_id,
            source_upload_ids=unique_upload_ids,
            created_at=now,
            updated_at=now,
        )
        await self._repository.put(discovery_case)
        return discovery_case

    async def get_case(
        self,
        *,
        session_id: str,
        requesting_user_id: str,
    ) -> DiscoveryCase:
        await self._session_service.get_session(
            session_id=session_id,
            requesting_user_id=requesting_user_id,
        )
        discovery_case = await self._repository.get_for_session(session_id=session_id)
        if discovery_case is None:
            raise DiscoveryCaseNotFoundError(
                f"No Discovery case exists for session '{session_id}'."
            )
        if discovery_case.owner_user_id != requesting_user_id:
            raise SessionAccessDeniedError(
                f"User '{requesting_user_id}' is not authorized to access "
                f"Discovery case '{discovery_case.id}'."
            )
        return discovery_case

    async def list_cases(self, *, owner_user_id: str) -> list[DiscoveryCase]:
        return await self._repository.list_for_owner(owner_user_id=owner_user_id)

    async def delete_case(
        self,
        *,
        session_id: str,
        requesting_user_id: str,
    ) -> None:
        discovery_case = await self.get_case(
            session_id=session_id,
            requesting_user_id=requesting_user_id,
        )
        await self._repository.delete(discovery_case_id=discovery_case.id)


def create_discovery_service(
    *,
    session_service: SessionService,
    repository: DiscoveryCaseRepository | None = None,
) -> DiscoveryService:
    return DiscoveryService(
        session_service=session_service,
        repository=repository or InMemoryDiscoveryCaseRepository(),
    )