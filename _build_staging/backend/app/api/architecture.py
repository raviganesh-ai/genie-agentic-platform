"""Architecture Studio API routes.

Thin wrapper over ``ArchitectureService``: the current interactive
architecture view for a workflow run, and alternative design generation
(routed through ``AgentOrchestrator.request_reanalysis``).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import get_architecture_service
from app.models.reanalysis_models import ReanalysisResult
from app.security.auth_models import AuthenticatedUser
from app.security.dependencies import get_current_user
from app.services.architecture_service import (
    ArchitectureService,
    ArchitectureSnapshot,
)

router = APIRouter(prefix="/sessions/{session_id}/architecture", tags=["architecture"])


class RequestAlternativeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str = Field(min_length=1)
    rationale: str = Field(default="")


@router.get("/{workflow_run_id}")
async def get_architecture(
    session_id: str,
    workflow_run_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    architecture_service: ArchitectureService = Depends(get_architecture_service),
) -> ArchitectureSnapshot:
    return await architecture_service.get_architecture(
        session_id=session_id, requesting_user_id=user.user_id, workflow_run_id=workflow_run_id
    )


@router.post("/{workflow_run_id}/alternative")
async def request_alternative(
    session_id: str,
    workflow_run_id: str,
    body: RequestAlternativeRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    architecture_service: ArchitectureService = Depends(get_architecture_service),
) -> ReanalysisResult:
    return await architecture_service.request_alternative(
        session_id=session_id,
        requesting_user_id=user.user_id,
        workflow_run_id=workflow_run_id,
        trace_id=body.trace_id,
        rationale=body.rationale,
    )
