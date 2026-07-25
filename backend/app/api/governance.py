"""Governance API routes.

Thin, read-only wrapper over ``GovernanceService.events_for_session`` - the
full governance/audit trail (agent registration, executions, communication,
memory reads/writes, tool requests, policy evaluations, denied access) for
one session, per the Governance Requirements in
``.github/copilot-instructions.md``. Also exposes one write endpoint,
``POST /checkpoints/confirm``, so the Discovery Wizard's Responsible AI
Accountability "Proceed to Next Step" gate is permanently auditable.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import get_governance_service, get_session_service
from app.governance.governance_service import GovernanceService
from app.models.governance_event import GovernanceEvent
from app.security.auth_models import AuthenticatedUser
from app.security.dependencies import get_current_user
from app.services.session_service import SessionService

router = APIRouter(prefix="/sessions/{session_id}/governance", tags=["governance"])


class ConfirmCheckpointRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str = Field(min_length=1)
    stage_key: str = Field(min_length=1)
    stage_label: str = Field(min_length=1)


@router.get("/events")
async def list_governance_events(
    session_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
    governance_service: GovernanceService = Depends(get_governance_service),
) -> list[GovernanceEvent]:
    await session_service.get_session(session_id=session_id, requesting_user_id=user.user_id)
    return await governance_service.events_for_session(session_id)


@router.post("/checkpoints/confirm")
async def confirm_checkpoint(
    session_id: str,
    body: ConfirmCheckpointRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
    governance_service: GovernanceService = Depends(get_governance_service),
) -> GovernanceEvent:
    await session_service.get_session(session_id=session_id, requesting_user_id=user.user_id)
    return await governance_service.record_human_checkpoint_confirmation(
        session_id=session_id,
        trace_id=body.trace_id,
        stage_key=body.stage_key,
        stage_label=body.stage_label,
        confirmed_by=user.user_id,
    )
