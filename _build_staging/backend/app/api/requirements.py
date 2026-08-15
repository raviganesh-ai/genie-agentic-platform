"""Requirement Discovery API routes.

Thin wrapper over ``RequirementsService``: exposes whether the requirements
captured for a workflow run qualify for a multi-agent agentic AI workflow,
and why - so the Requirement Discovery Map can gracefully inform the user
instead of silently proceeding when a simpler, non-agentic solution would
do.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import get_requirements_service
from app.models.requirements_qualification import RequirementsQualification
from app.security.auth_models import AuthenticatedUser
from app.security.dependencies import get_current_user
from app.services.requirements_service import RequirementsService

router = APIRouter(prefix="/sessions/{session_id}/requirements", tags=["requirements"])


@router.get("/{workflow_run_id}/qualification")
async def get_qualification(
    session_id: str,
    workflow_run_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    requirements_service: RequirementsService = Depends(get_requirements_service),
) -> RequirementsQualification:
    return await requirements_service.get_qualification(
        session_id=session_id, requesting_user_id=user.user_id, workflow_run_id=workflow_run_id
    )
