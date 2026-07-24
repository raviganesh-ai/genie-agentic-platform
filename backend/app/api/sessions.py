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
    dedicated_agents_provisioned: int = Field(
        description=(
            "Number of dedicated Azure AI Foundry agents provisioned for "
            "this customer's session (see CustomerAgentProvisioningService). "
            "0 when per-customer provisioning is not configured (local/dev "
            "without Foundry configured) - the session then falls back to "
            "the shared catalog agent pool."
        )
    )


class CxAccessClosed(BaseModel):
    """Confirmation that a customer session's access has been closed."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    dedicated_agents_deprovisioned: bool


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

    Also provisions a dedicated Azure AI Foundry agent fleet for this
    customer session (idempotent - safe to call again for the same
    session), so every further customer chat/reanalysis interaction routes
    to agents no other customer's session can reach. See
    ``POST /{session_id}/cx-access/close`` to tear those agents down.
    """

    await session_service.get_session(session_id=session_id, requesting_user_id=user.user_id)

    run = orchestrator.get_workflow_run(body.workflow_run_id)
    if run is None or run.session_id != session_id:
        raise UnknownWorkflowRunError(
            f"Workflow run '{body.workflow_run_id}' does not belong to session '{session_id}'."
        )

    provisioned = await orchestrator.provision_customer_agents(session_id=session_id)

    token = cx_token_service.mint(
        session_id=session_id,
        workflow_run_id=body.workflow_run_id,
        ttl_seconds=settings.cx_token_ttl_seconds,
    )
    claims = cx_token_service.validate(token)
    return CxAccessLink(
        path=f"/cx/{session_id}/app?t={token}",
        expires_at=claims.expires_at,
        dedicated_agents_provisioned=len(provisioned),
    )


@router.post("/{session_id}/cx-access/close", status_code=200)
async def close_cx_access(
    session_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
    orchestrator: AgentOrchestrator = Depends(get_agent_orchestrator),
) -> CxAccessClosed:
    """Explicitly end a customer session's cx access and tear down its agents.

    Deprovisions (deletes) every dedicated Foundry agent created for this
    session by ``POST /{session_id}/cx-access`` - never on an idle timeout,
    only on this explicit staff action. A no-op (still returns 200) if
    nothing was ever provisioned for this session. Previously minted
    tokens remain cryptographically valid until their own expiry, but any
    further step execution for this session will fail once its dedicated
    agents no longer exist.
    """

    await session_service.get_session(session_id=session_id, requesting_user_id=user.user_id)
    was_provisioned = orchestrator.customer_agents_provisioned(session_id)
    await orchestrator.deprovision_customer_agents(session_id=session_id)
    return CxAccessClosed(session_id=session_id, dedicated_agents_deprovisioned=was_provisioned)
