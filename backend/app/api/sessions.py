"""Session API routes: create, get, list, resume.

Thin wrappers over ``SessionService`` - every request is authenticated
(``get_current_user``) and every session-scoped route is authorized by the
service (ownership check), per the Security Requirements in
``.github/copilot-instructions.md``. Domain errors raised by the service
(unknown/unauthorized session) propagate to the centrally registered
``domain_error_handler`` (see ``app.main``) - routes do not translate
errors themselves.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import get_session_service
from app.models.session_models import Session
from app.models.workflow_models import WorkflowRunResult
from app.security.auth_models import AuthenticatedUser
from app.security.dependencies import get_current_user
from app.services.session_service import SessionService

router = APIRouter(prefix="/sessions", tags=["sessions"])


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)


class ResumeSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_run_id: str = Field(min_length=1)
    trace_id: str | None = None


@router.post("", status_code=201)
async def create_session(
    body: CreateSessionRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
) -> Session:
    return await session_service.create_session(owner_user_id=user.user_id, title=body.title)


@router.get("")
async def list_sessions(
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
) -> list[Session]:
    return await session_service.list_sessions(owner_user_id=user.user_id)


@router.get("/{session_id}")
async def get_session(
    session_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
) -> Session:
    return await session_service.get_session(session_id=session_id, requesting_user_id=user.user_id)


@router.post("/{session_id}/resume")
async def resume_session(
    session_id: str,
    body: ResumeSessionRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
) -> WorkflowRunResult:
    return await session_service.resume_session(
        session_id=session_id,
        requesting_user_id=user.user_id,
        workflow_run_id=body.workflow_run_id,
        trace_id=body.trace_id,
    )
