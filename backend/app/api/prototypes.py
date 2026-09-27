"""Self-service prototype inventory and cleanup for the authenticated user.

Distinct from ``app.api.prototype_admin`` (which is genie-admin-only and
spans every user's prototypes): these routes let an ordinary authenticated
user see and delete their OWN prototype runs across every session they own,
so hitting ``DeploymentPipelineService``'s per-owner active-prototype quota
(see ``pipeline_service.start``'s "Active prototype limit reached" error)
is self-serviceable instead of a dead end that requires an admin.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.api.dependencies import get_deployment_pipeline_service
from app.deploy_launch.models import DeploymentPipelineRun
from app.deploy_launch.pipeline_service import (
    DeploymentPipelineService,
    DeploymentPipelineStepFailedError,
)
from app.security.auth_models import AuthenticatedUser
from app.security.dependencies import get_current_user

router = APIRouter(prefix="/prototypes/mine", tags=["prototypes"])


@router.get("")
async def list_my_prototypes(
    user: AuthenticatedUser = Depends(get_current_user),
    pipeline_service: DeploymentPipelineService = Depends(get_deployment_pipeline_service),
) -> list[DeploymentPipelineRun]:
    return pipeline_service.list_runs_for_owner(user.user_id)


@router.delete("/{pipeline_run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_prototype(
    pipeline_run_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    pipeline_service: DeploymentPipelineService = Depends(get_deployment_pipeline_service),
) -> Response:
    run = pipeline_service.get_run(pipeline_run_id)
    if run is None or run.owner_user_id != user.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No prototype '{pipeline_run_id}' found.",
        )
    try:
        await pipeline_service.abandon(pipeline_run_id=pipeline_run_id, mission_title=run.mission_title)
    except DeploymentPipelineStepFailedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
