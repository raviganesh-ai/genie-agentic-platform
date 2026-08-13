"""Deploy & Launch pipeline API routes.

Exposes the real, nine-step Deploy & Launch pipeline
(``app.deploy_launch.pipeline_service.DeploymentPipelineService``):
``POST .../start`` (executes every step as soon as the human clicks
Start - the one gate this stage has), ``GET .../{pipeline_run_id}`` (poll
one run's current status), ``GET .../`` (list every run for this
session), and ``GET .../{pipeline_run_id}/download`` (a zip of the materialized backend
build plus the generated least-access policy document). Live per-step
progress is available via the existing
``GET /sessions/{session_id}/workflow-events/stream`` SSE route - this
pipeline publishes to the same ``WorkflowEventBus``.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import get_deployment_pipeline_service, get_session_service
from app.deploy_launch.models import DeploymentPipelineRun
from app.deploy_launch.pipeline_service import DeploymentPipelineService
from app.security.auth_models import AuthenticatedUser
from app.security.dependencies import get_current_user
from app.services.session_service import SessionService
from app.services.workshop_service import UnknownWorkflowRunError

router = APIRouter(prefix="/sessions/{session_id}/deploy-launch", tags=["deploy-launch"])


class StartDeploymentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_run_id: str = Field(min_length=1)
    trace_id: str | None = None


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
    await session_service.get_session(session_id=session_id, requesting_user_id=user.user_id)
    return await pipeline_service.start(
        session_id=session_id,
        requesting_user_id=user.user_id,
        workflow_run_id=body.workflow_run_id,
        trace_id=body.trace_id,
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
