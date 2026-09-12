"""Governance event seam for memory operations.

Every memory read, write, update, and denial emits an event (see
"Governance Requirements" in ``.github/copilot-instructions.md``). A full
governance provider (``Agent365GovernanceProvider`` /
``LocalGovernanceTraceProvider``) is implemented in Phase 5; this module
follows the same seam pattern already established for agent execution in
Phase 3 (``app.agents.gateway.GovernanceTraceRecorder`` /
``NullGovernanceTraceRecorder``) so call sites will not need to change once
Phase 5 lands.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from app.agents.models import MemoryTier

MemoryEventType = Literal[
    "memory_read",
    "memory_write",
    "memory_update",
    "memory_delete",
    "memory_denied",
]


@dataclass(frozen=True)
class MemoryGovernanceEvent:
    """A single governance-traceable memory operation event."""

    event_type: MemoryEventType
    tier: MemoryTier
    session_id: str
    agent_id: str
    trace_id: str
    timestamp: datetime
    detail: str = ""


class MemoryGovernanceRecorder(Protocol):
    """Seam for recording governance trace events during memory operations."""

    def record(self, event: MemoryGovernanceEvent) -> None: ...


class NullMemoryGovernanceRecorder:
    """No-op ``MemoryGovernanceRecorder`` used until Phase 5 governance services exist."""

    def record(self, event: MemoryGovernanceEvent) -> None:
        return None


@dataclass
class InMemoryMemoryGovernanceRecorder:
    """Captures every emitted event in-process.

    Not a governance provider itself - a minimal, dependency-free recorder
    useful for local development and tests to assert which events were
    emitted. Phase 5 supersedes this with a real
    ``Agent365GovernanceProvider`` / ``LocalGovernanceTraceProvider``.
    """

    events: list[MemoryGovernanceEvent] = field(default_factory=list)

    def record(self, event: MemoryGovernanceEvent) -> None:
        self.events.append(event)
