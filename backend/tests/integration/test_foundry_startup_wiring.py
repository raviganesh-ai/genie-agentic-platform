"""Integration test: Phase 10A Foundry services are wired into app startup.

Extends the fail-closed startup coverage in ``test_app_startup.py`` to
confirm ``FoundryAgentInventoryService``/``FoundryAgentLifecycleService``
are constructed and seeded during a normal local-mode startup, without
touching Azure AI Foundry (local settings never use
``AzureAgentGateway``, so ``app.state.foundry_synchronization_service``
stays ``None``).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.foundry_agent_inventory_service import FoundryAgentInventoryService
from app.services.foundry_agent_lifecycle_service import FoundryAgentLifecycleService


def test_foundry_inventory_and_lifecycle_services_are_available_after_local_startup(
    app_local_settings,
):
    app = create_app(settings=app_local_settings)

    with TestClient(app) as client:
        ready = client.get("/health/ready")
        assert ready.status_code == 200

        assert isinstance(app.state.foundry_inventory_service, FoundryAgentInventoryService)
        assert isinstance(app.state.foundry_lifecycle_service, FoundryAgentLifecycleService)
        assert app.state.foundry_synchronization_service is None


async def test_enabled_agents_are_seeded_into_inventory_on_startup(app_local_settings):
    app = create_app(settings=app_local_settings)

    with TestClient(app):
        records = await app.state.foundry_inventory_service.list()

    assert any(record.agent_id == "test-agent" for record in records)
