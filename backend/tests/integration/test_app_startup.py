"""Integration tests for FastAPI application startup and fail-closed behavior."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.governance.governance_models import GovernanceProviderError
from app.main import create_app
from app.validation.base import StartupValidationError


def test_app_starts_and_reports_ready_for_valid_local_settings(local_settings):
    app = create_app(settings=local_settings)
    with TestClient(app) as client:
        ready = client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json() == {"status": "ready"}

        live = client.get("/health/live")
        assert live.status_code == 200
        assert live.json() == {"status": "ok"}


def test_app_startup_fails_closed_without_injected_governance_provider(production_settings):
    """Valid production *configuration* alone is not enough to start serving.

    ``StartupValidationRunner`` passes (config is well-formed), but building
    the Phase 7 service graph also constructs a real ``GovernanceService``,
    which fails closed in production mode unless a concrete
    ``Agent365GovernanceProvider`` implementation is injected -- Genie has
    no built-in Agent365 SDK integration, so no fake/local provider is ever
    substituted in production. Injecting a real provider is done by the
    process wiring this app together (not by ``create_app`` itself).
    """

    app = create_app(settings=production_settings)
    with pytest.raises(GovernanceProviderError), TestClient(app):
        pass


def test_app_fails_closed_for_unsafe_production_settings(production_settings):
    unsafe = production_settings.model_copy(update={"allow_mock_agents": True})
    app = create_app(settings=unsafe)
    with pytest.raises(StartupValidationError), TestClient(app):
        pass


def test_app_fails_closed_when_required_config_missing(local_settings, tmp_path):
    missing = tmp_path / "no-config-here"
    broken = local_settings.model_copy(update={"config_root": missing})
    app = create_app(settings=broken)
    with pytest.raises(StartupValidationError), TestClient(app):
        pass
