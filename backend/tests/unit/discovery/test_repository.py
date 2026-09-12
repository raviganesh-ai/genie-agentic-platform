from __future__ import annotations

from datetime import UTC, datetime

from app.discovery.models import DiscoveryCase
from app.discovery.repository import InMemoryDiscoveryCaseRepository


async def test_in_memory_repository_returns_isolated_copies_and_deletes() -> None:
    repository = InMemoryDiscoveryCaseRepository()
    now = datetime.now(UTC)
    discovery_case = DiscoveryCase(
        id="discovery-1",
        session_id="session-1",
        owner_user_id="user-1",
        source_upload_ids=["upload-1"],
        created_at=now,
        updated_at=now,
    )

    await repository.put(discovery_case)
    loaded = await repository.get(discovery_case_id=discovery_case.id)
    assert loaded == discovery_case
    assert loaded is not discovery_case
    assert await repository.get_for_session(session_id="session-1") == discovery_case
    assert await repository.list_for_owner(owner_user_id="user-1") == [discovery_case]
    assert await repository.list_for_owner(owner_user_id="user-2") == []

    await repository.delete(discovery_case_id=discovery_case.id)

    assert await repository.get(discovery_case_id=discovery_case.id) is None