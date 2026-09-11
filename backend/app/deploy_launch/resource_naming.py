"""Deterministic Azure resource names for one deployed prototype."""
from __future__ import annotations

import hashlib


def prototype_resource_group_name(mission_slug: str) -> str:
    """Return the stable resource-group name used by one prototype."""

    return f"genie-proto-{mission_slug}"[:90].rstrip("-._()")


def prototype_frontend_environment_name(mission_slug: str) -> str:
    """Return a stable globally valid name for a prototype's public frontend environment."""

    digest = hashlib.sha256(mission_slug.encode("utf-8")).hexdigest()[:16]
    return f"genie-fe-{digest}"
