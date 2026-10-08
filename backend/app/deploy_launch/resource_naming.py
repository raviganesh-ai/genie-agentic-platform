"""Deterministic Azure resource names for one deployed prototype."""
from __future__ import annotations

import hashlib
import re

_CONTAINER_APP_NAME_MAX_LENGTH = 32


def prototype_container_app_name(mission_slug: str, component: str) -> str:
    """Return a stable Azure Container App name for one prototype component."""

    normalized_slug = re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]", "-", mission_slug.lower())).strip("-")
    normalized_component = re.sub(
        r"-+", "-", re.sub(r"[^a-z0-9-]", "-", component.lower())
    ).strip("-")
    candidate = f"genie-{normalized_slug or 'prototype'}-{normalized_component or 'app'}"
    if len(candidate) <= _CONTAINER_APP_NAME_MAX_LENGTH:
        return candidate

    digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:8]
    suffix = f"-{normalized_component or 'app'}-{digest}"
    stem_length = _CONTAINER_APP_NAME_MAX_LENGTH - len("genie-") - len(suffix)
    stem = (normalized_slug or "prototype")[:stem_length].rstrip("-")
    return f"genie-{stem}{suffix}"


def prototype_resource_group_name(mission_slug: str) -> str:
    """Return the stable resource-group name used by one prototype."""

    return f"genie-proto-{mission_slug}"[:90].rstrip("-._()")


def prototype_repository_name(mission_slug: str) -> str:
    """Return a stable, GitHub-safe repository name for one mission's
    generated prototype code - the repository Deploy & Launch's
    ``commit-generated-repository`` step creates and pushes the
    materialized backend/frontend build into."""

    normalized_slug = re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]", "-", mission_slug.lower())).strip("-")
    return f"genie-proto-{normalized_slug or 'prototype'}"[:100].rstrip("-")


def prototype_frontend_environment_name(mission_slug: str) -> str:
    """Return a stable globally valid name for a prototype's public frontend environment."""

    digest = hashlib.sha256(mission_slug.encode("utf-8")).hexdigest()[:16]
    return f"genie-fe-{digest}"


def modernization_deployment_container_app_name(plan_id: str) -> str:
    """Return a stable Azure Container App name for a modernization plan's own
    real-deployment Container App - a distinct "genie-modsvc-" prefix keeps
    these apart from Genie's own prototype Container Apps
    (``prototype_container_app_name``) in the same subscription."""

    digest = hashlib.sha256(plan_id.encode("utf-8")).hexdigest()[:10]
    return f"genie-modsvc-{digest}"[:_CONTAINER_APP_NAME_MAX_LENGTH]
