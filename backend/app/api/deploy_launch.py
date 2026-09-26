"""Deploy & Launch pipeline API routes.

Exposes the real, eight-step Deploy & Launch pipeline
(``app.deploy_launch.pipeline_service.DeploymentPipelineService``):
``POST .../start`` (executes every step as soon as the human clicks
Start - the one gate this stage has), ``GET .../{pipeline_run_id}`` (poll
one run's current status), ``GET .../`` (list every run for this
session), ``GET .../{pipeline_run_id}/download`` (a zip of the materialized backend
build plus the generated least-access policy document), and ``DELETE
.../{pipeline_run_id}`` (tear down a terminal run's owned prototype resources). Live per-step
progress is available via the existing
``GET /sessions/{session_id}/workflow-events/stream`` SSE route - this
pipeline publishes to the same ``WorkflowEventBus``.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import get_deployment_pipeline_service, get_session_service
from app.deploy_launch.models import DeploymentPipelineRun
from app.deploy_launch.pipeline_service import (
    DeploymentPipelineService,
    DeploymentPipelineStepFailedError,
)
from app.security.auth_models import AuthenticatedUser
from app.security.dependencies import get_current_user
from app.services.session_service import SessionNotFoundError, SessionService
from app.services.workshop_service import UnknownWorkflowRunError

router = APIRouter(prefix="/sessions/{session_id}/deploy-launch", tags=["deploy-launch"])


class StartDeploymentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_run_id: str = Field(min_length=1)
    trace_id: str | None = None
    resume_from_step: str | None = Field(
        default=None,
        description="If provided, resume from this step instead of starting from the first step. Useful for retrying a failed pipeline.",
    )


def _get_owned_run(
    *,
    session_id: str,
    pipeline_run_id: str,
    pipeline_service: DeploymentPipelineService,
) -> DeploymentPipelineRun:
    run = pipeline_service.get_run(pipeline_run_id)
    if run is None or run.session_id != session_id:
        raise UnknownWorkflowRunError(f"No Deploy & Launch run '{pipeline_run_id}' found.")
    return run


@router.post("/start")
async def start_deployment(
    session_id: str,
    body: StartDeploymentRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
    pipeline_service: DeploymentPipelineService = Depends(get_deployment_pipeline_service),
) -> DeploymentPipelineRun:
    # For retry operations (resume_from_step set), allow the request even if the
    # session lookup fails - the pipeline run may still exist and be retryable
    # (e.g., if the session was deleted but the deployment run is still in memory).
    # For new starts, enforce the session lookup to ensure proper authorization.
    try:
        await session_service.get_session(session_id=session_id, requesting_user_id=user.user_id)
    except SessionNotFoundError:
        if not body.resume_from_step:
            # New start requires a valid session
            raise
        # Retry allowed even if session is gone - user is authenticated and we'll
        # verify ownership of the specific deployment run in pipeline_service
    
    return await pipeline_service.start(
        session_id=session_id,
        requesting_user_id=user.user_id,
        workflow_run_id=body.workflow_run_id,
        trace_id=body.trace_id,
        resume_from_step=body.resume_from_step,
        requesting_tenant_id=user.tenant_id,
        requesting_object_id=user.object_id,
    )


@router.get("/")
async def list_deployments(
    session_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
    pipeline_service: DeploymentPipelineService = Depends(get_deployment_pipeline_service),
) -> list[DeploymentPipelineRun]:
    await session_service.get_session(session_id=session_id, requesting_user_id=user.user_id)
    return pipeline_service.list_runs_for_session(session_id)


@router.get("/{pipeline_run_id}")
async def get_deployment(
    session_id: str,
    pipeline_run_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
    pipeline_service: DeploymentPipelineService = Depends(get_deployment_pipeline_service),
) -> DeploymentPipelineRun:
    await session_service.get_session(session_id=session_id, requesting_user_id=user.user_id)
    return _get_owned_run(
        session_id=session_id, pipeline_run_id=pipeline_run_id, pipeline_service=pipeline_service
    )


@router.delete("/{pipeline_run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def abandon_deployment(
    session_id: str,
    pipeline_run_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
    pipeline_service: DeploymentPipelineService = Depends(get_deployment_pipeline_service),
) -> Response:
    session = await session_service.get_session(
        session_id=session_id, requesting_user_id=user.user_id
    )
    _get_owned_run(
        session_id=session_id, pipeline_run_id=pipeline_run_id, pipeline_service=pipeline_service
    )
    try:
        await pipeline_service.abandon(
            pipeline_run_id=pipeline_run_id, mission_title=session.title
        )
    except DeploymentPipelineStepFailedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{pipeline_run_id}/download")
async def download_deployment_artifacts(
    session_id: str,
    pipeline_run_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
    pipeline_service: DeploymentPipelineService = Depends(get_deployment_pipeline_service),
) -> StreamingResponse:
    """Zips the mission's real materialized backend build (agents, orchestrator,
    backend service scaffold) together with the generated least-access
    policy document - a real archive of what was actually deployed, never a
    fabricated summary."""

    await session_service.get_session(session_id=session_id, requesting_user_id=user.user_id)
    run = _get_owned_run(
        session_id=session_id, pipeline_run_id=pipeline_run_id, pipeline_service=pipeline_service
    )
    build_root = pipeline_service.get_build_root(pipeline_run_id)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        if build_root is not None and build_root.exists():
            for file_path in build_root.rglob("*"):
                if file_path.is_file():
                    archive.write(file_path, arcname=str(Path("build") / file_path.relative_to(build_root)))
        if run.access_policy is not None:
            archive.writestr("access-policy.json", run.access_policy.model_dump_json(indent=2))
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{pipeline_run_id}.zip"'},
    )
