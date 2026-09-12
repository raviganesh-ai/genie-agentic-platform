from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.discovery.models import DiscoveryCase
from app.discovery.repository import InMemoryDiscoveryCaseRepository
from app.discovery.service import DiscoveryService, DiscoveryStateConflictError
from app.repositories.session_repository import InMemorySessionRepository
from app.repositories.upload_repository import InMemoryUploadRepository
from app.services.session_service import SessionAccessDeniedError, SessionService


def _services() -> tuple[DiscoveryService, InMemoryDiscoveryCaseRepository, SessionService]:
    repository = InMemoryDiscoveryCaseRepository()
    session_service = SessionService(
        orchestrator=object(),  # type: ignore[arg-type]
        session_repository=InMemorySessionRepository(),
        upload_repository=InMemoryUploadRepository(),
    )
    return (
        DiscoveryService(session_service=session_service, repository=repository),
        repository,
        session_service,
    )


async def test_create_or_resume_merges_new_source_uploads_once() -> None:
    service, _, session_service = _services()
    session = await session_service.create_session(owner_user_id="user-1", title="Discovery")
    first_upload = await session_service.register_upload(
        session_id=session.id,
        requesting_user_id="user-1",
        upload_type="transcript",
        file_name="call.txt",
        content_type="text/plain",
        size_bytes=4,
    )
    second_upload = await session_service.register_upload(
        session_id=session.id,
        requesting_user_id="user-1",
        upload_type="supporting_document",
        file_name="notes.txt",
        content_type="text/plain",
        size_bytes=5,
    )

    created = await service.create_or_resume(
        session_id=session.id,
        requesting_user_id="user-1",
        source_upload_ids=[first_upload.id],
    )
    updated = await service.create_or_resume(
        session_id=session.id,
        requesting_user_id="user-1",
        source_upload_ids=[first_upload.id, second_upload.id, second_upload.id],
    )

    assert updated.id == created.id
    assert updated.source_upload_ids == [first_upload.id, second_upload.id]
    assert updated.version == created.version + 1


async def test_get_case_fails_closed_when_record_owner_is_inconsistent() -> None:
    service, repository, session_service = _services()
    session = await session_service.create_session(owner_user_id="user-1", title="Discovery")
    now = datetime.now(UTC)
    await repository.put(
        DiscoveryCase(
            id="discovery-1",
            session_id=session.id,
            owner_user_id="user-2",
            created_at=now,
            updated_at=now,
        )
    )

    with pytest.raises(SessionAccessDeniedError):
        await service.get_case(
            session_id=session.id,
            requesting_user_id="user-1",
        )


async def test_analyze_personas_rejects_legacy_raw_docx_before_foundry_execution() -> None:
    service, _, session_service = _services()
    session = await session_service.create_session(owner_user_id="user-1", title="Discovery")
    upload = await session_service.register_upload(
        session_id=session.id,
        requesting_user_id="user-1",
        upload_type="transcript",
        file_name="requirements.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size_bytes=128,
        status="completed",
        transcript_text="PK\x03\x04raw zip bytes decoded as text",
    )
    await service.create_or_resume(
        session_id=session.id,
        requesting_user_id="user-1",
        source_upload_ids=[upload.id],
    )

    with pytest.raises(DiscoveryStateConflictError, match="Start a new Discovery"):
        await service.analyze_personas(
            session_id=session.id,
            requesting_user_id="user-1",
        )