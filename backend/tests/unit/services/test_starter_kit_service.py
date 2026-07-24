"""Unit tests for StarterKitService."""
from __future__ import annotations

import io
import zipfile

import pytest

from app.config.settings import Settings
from app.services.starter_kit_service import StarterKitError, StarterKitService


def _settings(**overrides: object) -> Settings:
    defaults = {
        "environment": "development",
        "provider_mode": "local",
        "governance_provider": "local",
        "allow_mock_agents": True,
        "allow_local_agents": True,
        "use_synthetic_data": True,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def test_build_zip_contains_prototype_readme_and_access_policy():
    service = StarterKitService()

    archive_bytes = service.build_zip(
        prototype_html="<html>hello</html>",
        settings=_settings(cx_token_ttl_seconds=120, cx_reanalysis_rate_limit_per_hour=5),
        dedicated_agent_count=3,
    )

    archive = zipfile.ZipFile(io.BytesIO(archive_bytes))
    assert set(archive.namelist()) == {"prototype/index.html", "README.md", "ACCESS_POLICY.md"}
    assert archive.read("prototype/index.html").decode("utf-8") == "<html>hello</html>"

    policy_text = archive.read("ACCESS_POLICY.md").decode("utf-8")
    assert "120 seconds" in policy_text
    assert "5 requests per hour" in policy_text
    assert "Dedicated Azure AI Foundry agents provisioned for this session: 3" in policy_text

    readme_text = archive.read("README.md").decode("utf-8")
    assert "prototype/index.html" in readme_text


def test_build_zip_raises_when_prototype_is_empty():
    service = StarterKitService()

    with pytest.raises(StarterKitError):
        service.build_zip(prototype_html="", settings=_settings(), dedicated_agent_count=0)
