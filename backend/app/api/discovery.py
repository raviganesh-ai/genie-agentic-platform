"""Authenticated API routes for durable Discovery case lifecycle."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import get_discovery_service
from app.discovery.models import DiscoveryCase
from app.discovery.service import DiscoveryService
from app.security.auth_models import AuthenticatedUser
from app.security.dependencies import get_current_user

router = APIRouter(tags=["discovery"])


class CreateDiscoveryCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_upload_ids: list[str] = Field(default_factory=list)


@router.get("/discovery")
async def list_discovery_cases(
    user: AuthenticatedUser = Depends(get_current_user),
    discovery_service: DiscoveryService = Depends(get_discovery_service),
) -> list[DiscoveryCase]:
    return await discovery_service.list_cases(owner_user_id=user.user_id)


@router.post("/sessions/{session_id}/discovery", status_code=status.HTTP_201_CREATED)
async def create_or_resume_discovery_case(
    session_id: str,
    body: CreateDiscoveryCaseRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    discovery_service: DiscoveryService = Depends(get_discovery_service),
) -> DiscoveryCase:
    return await discovery_service.create_or_resume(
        session_id=session_id,
        requesting_user_id=user.user_id,
        source_upload_ids=body.source_upload_ids,
    )


@router.get("/sessions/{session_id}/discovery")
async def get_discovery_case(
    session_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    discovery_service: DiscoveryService = Depends(get_discovery_service),
) -> DiscoveryCase:
    return await discovery_service.get_case(
        session_id=session_id,
        requesting_user_id=user.user_id,
    )


@router.delete(
    "/sessions/{session_id}/discovery",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_discovery_case(
    session_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    discovery_service: DiscoveryService = Depends(get_discovery_service),
) -> Response:
    await discovery_service.delete_case(
        session_id=session_id,
        requesting_user_id=user.user_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)