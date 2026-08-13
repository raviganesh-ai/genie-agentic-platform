"""Integration tests for FastAPI application startup and fail-closed behavior."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

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


def test_app_fails_closed_when_required_config_missing(local_settings, tmp_path):
    missing = tmp_path / "no-config-here"
    broken = local_settings.model_copy(update={"config_root": missing})
    app = create_app(settings=broken)
    with pytest.raises(StartupValidationError), TestClient(app):
        pass
