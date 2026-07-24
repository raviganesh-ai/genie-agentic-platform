"""Unit tests for the pure ``_uses_azure_agent_gateway`` startup gate helper.

This mirrors ``app.agents.gateway.create_agent_gateway``'s resolution logic
without constructing anything, so it is safe to test directly against
``Settings`` without touching Azure AI Foundry.
"""
from __future__ import annotations

from app.main import _uses_azure_agent_gateway


def test_production_always_uses_azure_gateway(production_settings):
    assert _uses_azure_agent_gateway(production_settings) is True


def test_local_mode_with_local_agents_allowed_does_not_use_azure_gateway(local_settings):
    assert local_settings.allow_local_agents is True
    assert _uses_azure_agent_gateway(local_settings) is False


def test_local_mode_without_local_agents_and_foundry_configured_uses_azure_gateway(local_settings):
    settings = local_settings.model_copy(
        update={
            "allow_local_agents": False,
            "azure_foundry_endpoint": "https://genie-foundry.example-project.azure.com",
            "azure_foundry_project_name": "genie-project",
        }
    )
    assert _uses_azure_agent_gateway(settings) is True


def test_local_mode_without_local_agents_and_no_foundry_configured_does_not_use_azure_gateway(
    local_settings,
):
    settings = local_settings.model_copy(update={"allow_local_agents": False})
    assert _uses_azure_agent_gateway(settings) is False
