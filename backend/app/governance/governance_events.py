"""Governance event construction helpers.

Centralizes ``GovernanceEvent`` id/timestamp generation so every category
of governed activity (see "Agent Execution Governance" in
``.github/copilot-instructions.md``) is constructed consistently across
``GovernanceService`` and any future caller.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.models.governance_event import GovernanceEvent, GovernanceEventCategory

__all__ = ["new_governance_event"]


def new_governance_event(
    category: GovernanceEventCategory,
    *,
    session_id: str,
    trace_id: str,
    agent_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> GovernanceEvent:
    """Build a new ``GovernanceEvent`` with a fresh id and current timestamp."""

    return GovernanceEvent(
        id=str(uuid4()),
        category=category,
        session_id=session_id,
        trace_id=trace_id,
        agent_id=agent_id,
        timestamp=datetime.now(UTC),
        detail=detail or {},
    )
