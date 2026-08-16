"""Model catalog API routes.

Lists model deployments available to the current Genie backend deployment.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import get_model_catalog_service
from app.security.auth_models import AuthenticatedUser
from app.security.dependencies import get_current_user
from app.services.model_catalog_service import ModelCatalogService

router = APIRouter(prefix="/models", tags=["models"])


class ModelCatalogResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_model: str = Field(min_length=1)
    available_models: list[str] = Field(default_factory=list)


@router.get("/available")
async def list_available_models(
    _: AuthenticatedUser = Depends(get_current_user),
    model_catalog_service: ModelCatalogService = Depends(get_model_catalog_service),
) -> ModelCatalogResponse:
    catalog = await model_catalog_service.get_catalog()
    return ModelCatalogResponse(
        default_model=catalog.default_model,
        available_models=catalog.available_models,
    )
