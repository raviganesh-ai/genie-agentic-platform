"""Governance provider interfaces and the local development implementation.

See "AGENT365" and "Governance Requirements" in
``.github/copilot-instructions.md``: ``Agent365GovernanceProvider`` is an
interface only - Genie has no documented Agent365 SDK to call today, so no
concrete production implementation is provided here. A real integration
must be supplied externally (via ``create_governance_service``'s
``provider=`` parameter) once Agent365 SDK/API access exists.
``LocalGovernanceTraceProvider`` is a concrete, in-memory implementation
for local development and tests only; production must never use it.
"""
from __future__ import annotations

from typing import Protocol

from app.models.approval_models import ApprovalAuditRecord
from app.models.governance_event import GovernanceEvent


class GovernanceProviderError(RuntimeError):
    """Raised when no usable governance provider is available (fail closed)."""


class GovernanceProvider(Protocol):
    """The seam every governance provider (Agent365 or local) must satisfy."""

    def record(self, event: GovernanceEvent) -> None:
        """Record a single governance event (agent execution, memory access, ...)."""
        ...

    def record_approval_audit(self, record: ApprovalAuditRecord) -> None:
        """Record a single approval audit entry (requested/approved/rejected/expired)."""
        ...


class Agent365GovernanceProvider(GovernanceProvider, Protocol):
    """The interface a real Microsoft Agent365 governance integration must implement.

    Interface only, per the Phase 5 instructions: no undocumented Agent365
    SDK calls are invented here. A concrete implementation must be provided
    by the caller once real Agent365 SDK/API access is available;
    production startup fails closed (see ``GovernanceProviderValidator`` and
    ``create_governance_service``) until one is wired in.
    """


class LocalGovernanceTraceProvider:
    """In-memory ``GovernanceProvider`` for local development and tests only.

    Never selected in production: ``GovernanceProviderValidator`` fails
    startup closed if ``governance_provider`` is not ``"agent365"`` in
    production, and ``create_governance_service`` never returns this
    provider in production regardless of that setting.
    """

    def __init__(self) -> None:
        self.events: list[GovernanceEvent] = []
        self.approval_audit_records: list[ApprovalAuditRecord] = []

    def record(self, event: GovernanceEvent) -> None:
        self.events.append(event)

    def record_approval_audit(self, record: ApprovalAuditRecord) -> None:
        self.approval_audit_records.append(record)
