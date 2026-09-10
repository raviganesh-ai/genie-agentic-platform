"""Administrative inventory and cleanup for every Genie-managed prototype."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.api.dependencies import get_deployment_pipeline_service
from app.deploy_launch.models import DeploymentPipelineRun
from app.deploy_launch.pipeline_service import (
    DeploymentPipelineService,
    DeploymentPipelineStepFailedError,
)
from app.security.auth_models import AuthenticatedUser
from app.security.dependencies import require_genie_admin

router = APIRouter(prefix="/api/admin/prototypes", tags=["prototype-admin"])


@router.get("")
async def list_prototypes(
    _: AuthenticatedUser = Depends(require_genie_admin),
    pipeline_service: DeploymentPipelineService = Depends(get_deployment_pipeline_service),
) -> list[DeploymentPipelineRun]:
    return pipeline_service.list_all_runs()


@router.delete("/{pipeline_run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_prototype(
    pipeline_run_id: str,
    _: AuthenticatedUser = Depends(require_genie_admin),
    pipeline_service: DeploymentPipelineService = Depends(get_deployment_pipeline_service),
) -> Response:
    try:
        await pipeline_service.abandon(pipeline_run_id=pipeline_run_id)
    except DeploymentPipelineStepFailedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)