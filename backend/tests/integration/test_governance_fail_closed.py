"""Integration tests: application startup fails closed on bad Phase 5 governance config."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.validation.base import StartupValidationError


def test_app_fails_closed_when_governance_policy_missing(local_settings):
    (local_settings.policies_path / "governance_policy.yaml").unlink()
    app = create_app(settings=local_settings)
    with pytest.raises(StartupValidationError), TestClient(app):
        pass


def test_app_fails_closed_when_approval_policy_missing(local_settings):
    (local_settings.policies_path / "approval_policy.yaml").unlink()
    app = create_app(settings=local_settings)
    with pytest.raises(StartupValidationError), TestClient(app):
        pass


def test_app_fails_closed_when_production_lineage_store_is_in_memory(production_settings):
    unsafe = production_settings.model_copy(update={"lineage_store_backend": "in_memory"})
    app = create_app(settings=unsafe)
    with pytest.raises(StartupValidationError), TestClient(app):
        pass


def test_app_fails_closed_when_production_lineage_store_endpoint_missing(production_settings):
    unsafe = production_settings.model_copy(update={"lineage_store_endpoint": None})
    app = create_app(settings=unsafe)
    with pytest.raises(StartupValidationError), TestClient(app):
        pass


def test_app_fails_closed_when_production_governance_provider_is_local(production_settings):
    unsafe = production_settings.model_copy(update={"governance_provider": "local"})
    app = create_app(settings=unsafe)
    with pytest.raises(StartupValidationError), TestClient(app):
        pass
