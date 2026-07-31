"""Unit tests for FrontendDeploymentService's factory (real vs Null selection)."""
from __future__ import annotations

import pytest

from app.config.settings import Settings
from app.deploy_launch.frontend_deployment_service import (
    FrontendDeploymentError,
    NullFrontendDeploymentService,
    create_frontend_deployment_service,
)


def _settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[call-arg]


async def test_local_mode_without_config_returns_null_service():
    service = create_frontend_deployment_service(settings=_settings(provider_mode="local"))

    assert isinstance(service, NullFrontendDeploymentService)


async def test_null_service_deploy_returns_a_local_placeholder_url():
    service = NullFrontendDeploymentService()

    result = await service.deploy(ui_root="unused")  # type: ignore[arg-type]

    assert result.frontend_url == "http://localhost/missions/frontend"


def test_production_mode_without_config_raises():
    with pytest.raises(FrontendDeploymentError):
        create_frontend_deployment_service(settings=_settings(provider_mode="production"))
