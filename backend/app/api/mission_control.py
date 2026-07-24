"""Mission Control API routes.

Exposes the ``MissionControlSnapshot`` - the primary UI contract - as one
full-dashboard endpoint plus focused sub-views (workflow progress, active
agents, timeline, approval status) that are simple projections of the same
snapshot, per the "Mission Control APIs" requirement. No aggregation
happens here: it is all performed by ``MissionControlService``.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import get_mission_control_service
from app.models.approval_models import ApprovalRequest
from app.models.mission_control_snapshot import MissionControlSnapshot, TimelineEntry
from app.security.auth_models import AuthenticatedUser
from app.security.dependencies import get_current_user
from app.services.mission_control_service import MissionControlService

router = APIRouter(prefix="/sessions/{session_id}/mission-control", tags=["mission-control"])


@router.get("")
async def get_snapshot(
    session_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    mission_control_service: MissionControlService = Depends(get_mission_control_service),
) -> MissionControlSnapshot:
    return await mission_control_service.get_snapshot(
        session_id=session_id, requesting_user_id=user.user_id
    )


@router.get("/progress")
async def get_workflow_progress(
    session_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    mission_control_service: MissionControlService = Depends(get_mission_control_service),
) -> dict[str, float | str | None]:
    snapshot = await mission_control_service.get_snapshot(
        session_id=session_id, requesting_user_id=user.user_id
    )
    return {
        "workflow_run_id": snapshot.workflow_run_id,
        "workflow_status": snapshot.workflow_status,
        "mission_progress": snapshot.mission_progress,
        "current_workflow_step": snapshot.current_workflow_step,
    }


@router.get("/active-agents")
async def get_active_agents(
    session_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    mission_control_service: MissionControlService = Depends(get_mission_control_service),
) -> dict[str, list[str]]:
    snapshot = await mission_control_service.get_snapshot(
        session_id=session_id, requesting_user_id=user.user_id
    )
    return {
        "active_agents": snapshot.active_agents,
        "completed_agents": snapshot.completed_agents,
        "blocked_agents": snapshot.blocked_agents,
    }


@router.get("/timeline")
async def get_timeline(
    session_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    mission_control_service: MissionControlService = Depends(get_mission_control_service),
) -> list[TimelineEntry]:
    snapshot = await mission_control_service.get_snapshot(
        session_id=session_id, requesting_user_id=user.user_id
    )
    return snapshot.timeline


@router.get("/approval-status")
async def get_approval_status(
    session_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    mission_control_service: MissionControlService = Depends(get_mission_control_service),
) -> list[ApprovalRequest]:
    snapshot = await mission_control_service.get_snapshot(
        session_id=session_id, requesting_user_id=user.user_id
    )
    return snapshot.approvals
