"""Unit tests for FoundryProjectService: validation and SDK error isolation.

These tests never contact a real Azure AI Foundry endpoint. Construction
failures are simulated by monkeypatching the azure-ai-projects SDK's
``AIProjectClient`` constructor.
"""
from __future__ import annotations

import pytest

from app.agents.foundry.errors import FoundryUnavailableError
from app.agents.foundry.project_service import FoundryProjectService


def test_rejects_blank_endpoint():
    with pytest.raises(FoundryUnavailableError, match="endpoint must not be blank"):
        FoundryProjectService(endpoint="  ", project_name="genie-project")


def test_rejects_blank_project_name():
    with pytest.raises(FoundryUnavailableError, match="project name must not be blank"):
        FoundryProjectService(endpoint="https://example.azure.com", project_name=" ")


def test_get_api_client_wraps_sdk_construction_failures(monkeypatch: pytest.MonkeyPatch):
    import azure.ai.projects as azure_ai_projects_module

    def _boom(*args: object, **kwargs: object):
        raise RuntimeError("bad credential")

    monkeypatch.setattr(azure_ai_projects_module, "AIProjectClient", _boom)

    service = FoundryProjectService(
        endpoint="https://genie-foundry.example-project.azure.com",
        project_name="genie-project",
    )

    with pytest.raises(FoundryUnavailableError, match="bad credential"):
        service.get_api_client()


def test_get_api_client_caches_the_client(monkeypatch: pytest.MonkeyPatch):
    import azure.ai.projects as azure_ai_projects_module

    call_count = {"n": 0}

    class _FakeSdkClient:
        def __init__(self, **kwargs: object) -> None:
            call_count["n"] += 1

    monkeypatch.setattr(azure_ai_projects_module, "AIProjectClient", _FakeSdkClient)

    service = FoundryProjectService(
        endpoint="https://genie-foundry.example-project.azure.com",
        project_name="genie-project",
    )

    first = service.get_api_client()
    second = service.get_api_client()

    assert first is second
    assert call_count["n"] == 1
