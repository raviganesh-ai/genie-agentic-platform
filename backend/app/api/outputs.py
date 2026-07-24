"""Output/Final Output Center API routes.

Thin wrapper over ``OutputService``: lists the supported deliverable types
(executive summary, requirements/architecture/roadmap packages, final
output package) and generates one from a completed workflow run.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import get_output_service
from app.models.workflow_models import DeliverablePackage, DeliverableType
from app.security.auth_models import AuthenticatedUser
from app.security.dependencies import get_current_user
from app.services.output_service import OutputService

router = APIRouter(prefix="/sessions/{session_id}/outputs", tags=["outputs"])


@router.get("")
async def list_supported_deliverable_types(
    session_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    output_service: OutputService = Depends(get_output_service),
) -> list[DeliverableType]:
    return output_service.supported_deliverable_types()


@router.get("/{workflow_run_id}/{deliverable_type}")
async def generate_deliverable(
    session_id: str,
    workflow_run_id: str,
    deliverable_type: DeliverableType,
    user: AuthenticatedUser = Depends(get_current_user),
    output_service: OutputService = Depends(get_output_service),
) -> DeliverablePackage:
    return await output_service.generate_deliverable(
        session_id=session_id,
        requesting_user_id=user.user_id,
        workflow_run_id=workflow_run_id,
        deliverable_type=deliverable_type,
    )
