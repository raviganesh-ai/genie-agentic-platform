"""Regression tests for prototype-owned Azure resource names."""
from __future__ import annotations

import re

from app.deploy_launch.resource_naming import prototype_container_app_name


def test_container_app_name_preserves_existing_short_names() -> None:
    assert prototype_container_app_name("claims-1234", "backend") == (
        "genie-claims-1234-backend"
    )
    assert prototype_container_app_name("claims-1234", "frontend") == (
        "genie-claims-1234-frontend"
    )


def test_container_app_name_shortens_overlength_workload_names_deterministically() -> None:
    backend_name = prototype_container_app_name("interal-demo-c0d89e27", "backend")
    frontend_name = prototype_container_app_name("interal-demo-c0d89e27", "frontend")

    assert backend_name == prototype_container_app_name("interal-demo-c0d89e27", "backend")
    assert frontend_name == prototype_container_app_name("interal-demo-c0d89e27", "frontend")
    assert backend_name != frontend_name
    assert len(backend_name) <= 32
    assert len(frontend_name) <= 32
    assert re.fullmatch(r"[a-z][a-z0-9-]*[a-z0-9]", backend_name)
    assert re.fullmatch(r"[a-z][a-z0-9-]*[a-z0-9]", frontend_name)
    assert "--" not in backend_name
    assert "--" not in frontend_name


def test_container_app_name_hash_prevents_truncation_collisions() -> None:
    first = prototype_container_app_name("customer-workload-alpha-c0d89e27", "backend")
    second = prototype_container_app_name("customer-workload-beta-c0d89e27", "backend")

    assert first != second