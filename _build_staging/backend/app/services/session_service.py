"""Session application service.

Owns session lifecycle (create/get/list/resume) and upload/ingestion
tracking - the two entities Phase 7 introduces that no prior phase
modeled. Every read/mutation of a specific session enforces ownership
(the caller's ``AuthenticatedUser.user_id`` must match
``Session.owner_user_id``), per "API routes must validate: ... session
ownership ... Unauthorized requests must fail." in
``.github/copilot-instructions.md``. Resuming a session's workflow
delegates to the unmodified Phase 6 ``AgentOrchestrator`` - this service
performs no orchestration logic itself.
"""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.models.session_models import Session
from app.models.upload_models import IngestionStatus, UploadRecord, UploadType
from app.models.workflow_models import WorkflowRunResult
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.repositories.session_repository import InMemorySessionRepository, SessionRepository
from app.repositories.upload_repository import InMemoryUploadRepository, UploadRepository

__all__ = [
    "SessionAccessDeniedError",
    "SessionNotFoundError",
    "SessionService",
    "UploadNotFoundError",
    "create_session_service",
]


class SessionNotFoundError(RuntimeError):
    """Raised when a referenced session id does not exist."""


class SessionAccessDeniedError(RuntimeError):
    """Raised when the caller does not own the referenced session."""


class UploadNotFoundError(RuntimeError):
    """Raised when a referenced upload id does not exist."""


class SessionService:
    """Creates, reads, lists, and resumes sessions; tracks uploads within them."""

    def __init__(
        self,
        *,
        orchestrator: AgentOrchestrator,
        session_repository: SessionRepository,
        upload_repository: UploadRepository,
    ) -> None:
        self._orchestrator = orchestrator
        self._session_repository = session_repository
        self._upload_repository = upload_repository

    async def create_session(self, *, owner_user_id: str, title: str) -> Session:
        now = datetime.now(UTC)
        session = Session(
            id=str(uuid4()),
            owner_user_id=owner_user_id,
            title=title,
            status="created",
            created_at=now,
            updated_at=now,
        )
        await self._session_repository.put(session)
        return session

    async def get_session(self, *, session_id: str, requesting_user_id: str) -> Session:
        return await self._get_owned_session(
            session_id=session_id, requesting_user_id=requesting_user_id
        )

    async def list_sessions(self, *, owner_user_id: str) -> list[Session]:
        return await self._session_repository.list_for_owner(owner_user_id=owner_user_id)

    async def resume_session(
        self,
        *,
        session_id: str,
        requesting_user_id: str,
        workflow_run_id: str,
        trace_id: str | None = None,
    ) -> WorkflowRunResult:
        session = await self._get_owned_session(
            session_id=session_id, requesting_user_id=requesting_user_id
        )
        result = await self._orchestrator.resume_workflow(
            workflow_run_id=workflow_run_id,
            session_id=session.id,
            trace_id=trace_id,
        )
        updated = session.model_copy(
            update={
                "status": "active",
                "updated_at": datetime.now(UTC),
                "latest_workflow_run_id": result.workflow_run_id,
            }
        )
        await self._session_repository.put(updated)
        return result

    async def register_upload(
        self,
        *,
        session_id: str,
        requesting_user_id: str,
        upload_type: UploadType,
        file_name: str,
        content_type: str,
        size_bytes: int,
        status: IngestionStatus = "received",
        detail: str = "",
        transcript_text: str | None = None,
    ) -> UploadRecord:
        await self._get_owned_session(
            session_id=session_id, requesting_user_id=requesting_user_id
        )
        now = datetime.now(UTC)
        record = UploadRecord(
            id=str(uuid4()),
            session_id=session_id,
            upload_type=upload_type,
            file_name=file_name,
            content_type=content_type,
            size_bytes=size_bytes,
            uploaded_by=requesting_user_id,
            status=status,
            detail=detail,
            transcript_text=transcript_text,
            uploaded_at=now,
            updated_at=now,
        )
        await self._upload_repository.put(record)
        return record

    async def get_upload(
        self, *, session_id: str, upload_id: str, requesting_user_id: str
    ) -> UploadRecord:
        await self._get_owned_session(
            session_id=session_id, requesting_user_id=requesting_user_id
        )
        record = await self._upload_repository.get(upload_id=upload_id)
        if record is None or record.session_id != session_id:
            raise UploadNotFoundError(f"Unknown upload id '{upload_id}' for session '{session_id}'.")
        return record

    async def list_uploads(
        self, *, session_id: str, requesting_user_id: str
    ) -> list[UploadRecord]:
        await self._get_owned_session(
            session_id=session_id, requesting_user_id=requesting_user_id
        )
        return await self._upload_repository.list_for_session(session_id=session_id)

    async def get_combined_transcript_text(
        self, *, session_id: str, requesting_user_id: str
    ) -> str:
        """Concatenates every completed upload's transcript text for a session.

        Used to auto-populate workflow step variables from whatever call
        transcripts/recordings have been uploaded and transcribed so far -
        callers never need to paste transcript text into a workflow run
        request by hand.
        """

        uploads = await self.list_uploads(
            session_id=session_id, requesting_user_id=requesting_user_id
        )
        texts = [
            upload.transcript_text
            for upload in sorted(uploads, key=lambda u: u.uploaded_at)
            if upload.transcript_text
        ]
        return "\n\n".join(texts)

    async def update_ingestion_status(
        self,
        *,
        session_id: str,
        upload_id: str,
        requesting_user_id: str,
        status: IngestionStatus,
        detail: str = "",
    ) -> UploadRecord:
        record = await self.get_upload(
            session_id=session_id, upload_id=upload_id, requesting_user_id=requesting_user_id
        )
        updated = record.model_copy(
            update={"status": status, "detail": detail, "updated_at": datetime.now(UTC)}
        )
        await self._upload_repository.put(updated)
        return updated

    async def _get_owned_session(self, *, session_id: str, requesting_user_id: str) -> Session:
        session = await self._session_repository.get(session_id=session_id)
        if session is None:
            raise SessionNotFoundError(f"Unknown session id '{session_id}'.")
        if session.owner_user_id != requesting_user_id:
            raise SessionAccessDeniedError(
                f"User '{requesting_user_id}' is not authorized to access session '{session_id}'."
            )
        return session


def create_session_service(
    *,
    orchestrator: AgentOrchestrator,
    session_repository: SessionRepository | None = None,
    upload_repository: UploadRepository | None = None,
) -> SessionService:
    """Build a ``SessionService``, defaulting to in-memory repositories for local/dev/test."""

    return SessionService(
        orchestrator=orchestrator,
        session_repository=session_repository or InMemorySessionRepository(),
        upload_repository=upload_repository or InMemoryUploadRepository(),
    )
