"""Unit tests for loading Azure resource provider requirements."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.deployment.resource_provider_requirements import (
    ResourceProviderRequirementError,
    load_resource_provider_requirements,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_loads_valid_requirements_file(tmp_path: Path):
    _write(
        tmp_path / "resource_providers.yaml",
        """
resource_providers:
  - namespace: Microsoft.Storage
    display_name: Azure Storage
    reason: Backs ingestion storage.
  - namespace: Microsoft.KeyVault
    display_name: Azure Key Vault
    reason: Stores secrets.
    required: false
""",
    )

    requirements = load_resource_provider_requirements(tmp_path)

    by_namespace = {r.namespace: r for r in requirements}
    assert len(requirements) == 2
    assert by_namespace["Microsoft.Storage"].required is True
    assert by_namespace["Microsoft.KeyVault"].required is False


def test_fails_closed_when_directory_missing(tmp_path: Path):
    with pytest.raises(ResourceProviderRequirementError, match="No resource provider"):
        load_resource_provider_requirements(tmp_path / "missing")


def test_fails_closed_on_duplicate_namespace(tmp_path: Path):
    _write(
        tmp_path / "a.yaml",
        "resource_providers:\n  - namespace: Microsoft.Storage\n"
        "    display_name: A\n    reason: r\n",
    )
    _write(
        tmp_path / "b.yaml",
        "resource_providers:\n  - namespace: Microsoft.Storage\n"
        "    display_name: B\n    reason: r\n",
    )

    with pytest.raises(ResourceProviderRequirementError, match="Duplicate resource provider"):
        load_resource_provider_requirements(tmp_path)


def test_fails_closed_on_missing_top_level_key(tmp_path: Path):
    _write(tmp_path / "bad.yaml", "not_the_right_key: []\n")

    with pytest.raises(ResourceProviderRequirementError, match="top-level 'resource_providers'"):
        load_resource_provider_requirements(tmp_path)


def test_fails_closed_on_invalid_entry(tmp_path: Path):
    _write(
        tmp_path / "bad.yaml",
        "resource_providers:\n  - namespace: Microsoft.Storage\n",
    )

    with pytest.raises(ResourceProviderRequirementError, match="Invalid resource provider"):
        load_resource_provider_requirements(tmp_path)
