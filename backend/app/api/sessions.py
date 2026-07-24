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

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import get_agent_orchestrator, get_cx_token_service, get_session_service
from app.config.settings import Settings, get_settings
from app.models.session_models import Session
from app.models.workflow_models import WorkflowRunResult
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.orchestration.workflow_execution_service import UnknownWorkflowRunError
from app.security.auth_models import AuthenticatedUser
from app.security.cx_tokens import CxTokenService
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


class CreateCxAccessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_run_id: str = Field(min_length=1)


class CxAccessLink(BaseModel):
    """A one-time, short-lived link to the customer-facing prototype surface.

    ``path`` is relative (no host) - the caller (an internal Mission
    Control operator) combines it with the backend's own public origin,
    avoiding a duplicate hardcoded base-url setting.
    """

    model_config = ConfigDict(extra="forbid")

    path: str
    expires_at: datetime


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


@router.post("/{session_id}/cx-access", status_code=201)
async def create_cx_access_link(
    session_id: str,
    body: CreateCxAccessRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
    orchestrator: AgentOrchestrator = Depends(get_agent_orchestrator),
    cx_token_service: CxTokenService = Depends(get_cx_token_service),
    settings: Settings = Depends(get_settings),
) -> CxAccessLink:
    """Mint a short-lived, session-scoped link to the customer prototype surface.

    Only an already-authenticated internal Mission Control user who owns
    the session may mint a link (``session_service.get_session`` enforces
    ownership, fails closed otherwise). The minted token is scoped to
    exactly this ``session_id`` and the given ``workflow_run_id`` - it is
    rejected by ``app/api/cx.py`` for any other session, and cannot access
    any internal API.
    """

    await session_service.get_session(session_id=session_id, requesting_user_id=user.user_id)

    run = orchestrator.get_workflow_run(body.workflow_run_id)
    if run is None or run.session_id != session_id:
        raise UnknownWorkflowRunError(
            f"Workflow run '{body.workflow_run_id}' does not belong to session '{session_id}'."
        )

    token = cx_token_service.mint(
        session_id=session_id,
        workflow_run_id=body.workflow_run_id,
        ttl_seconds=settings.cx_token_ttl_seconds,
    )
    claims = cx_token_service.validate(token)
    return CxAccessLink(path=f"/cx/{session_id}/app?t={token}", expires_at=claims.expires_at)
