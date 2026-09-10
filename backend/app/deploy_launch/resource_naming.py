"""Deterministic Azure resource names for one deployed prototype."""
from __future__ import annotations


def prototype_resource_group_name(mission_slug: str) -> str:
    """Return the stable resource-group name used by one prototype."""

    return f"genie-proto-{mission_slug}"[:90].rstrip("-._()")
