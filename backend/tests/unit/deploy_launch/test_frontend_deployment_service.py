"""Unit tests for FrontendDeploymentService's factory (real vs Null selection)."""
from __future__ import annotations

from app.config.settings import Settings
from app.deploy_launch.frontend_deployment_service import (
    NullFrontendDeploymentService,
    _is_authorization_permission_mismatch,
    create_frontend_deployment_service,
)


def _settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[call-arg]


async def test_local_mode_without_config_returns_null_service():
    service = create_frontend_deployment_service(settings=_settings())

    assert isinstance(service, NullFrontendDeploymentService)


async def test_null_service_deploy_returns_a_local_placeholder_url():
    service = NullFrontendDeploymentService()

    result = await service.deploy(ui_root="unused")  # type: ignore[arg-type]

    assert result.frontend_url == "http://localhost/missions/mission/frontend"


def test_detects_storage_authorization_permission_mismatch():
    exc = RuntimeError("ErrorCode:AuthorizationPermissionMismatch Content: <Error />")

    assert _is_authorization_permission_mismatch(exc)


def test_generated_frontend_build_uses_impeccable_compatible_node_runtime() -> None:
    from app.deploy_launch.container_app_frontend_deployment_service import (
        _FRONTEND_DOCKERFILE,
    )

    assert "FROM node:22-alpine AS build" in _FRONTEND_DOCKERFILE
    assert "RUN npm run build" in _FRONTEND_DOCKERFILE
