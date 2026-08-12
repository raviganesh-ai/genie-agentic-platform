"""Governance service: policy-driven, provider-backed event recording.

``GovernanceService`` is the single place agent execution, memory access,
tool use, policy evaluations, and denied access are recorded as
``GovernanceEvent`` records (see "AGENT EXECUTION GOVERNANCE" in the Phase
5 instructions and "Governance Requirements" in
``.github/copilot-instructions.md``). Every recorded event is both:

1. persisted to a session-queryable ``GovernanceEventRepository`` (used by
   ``TraceabilityService`` and ``ReplayService`` to reconstruct history),
   and
2. forwarded to the configured ``GovernanceProvider`` (Agent365 in
   production, ``LocalGovernanceTraceProvider`` in development/tests) so an
   external enterprise governance platform can independently observe it.

``create_governance_service`` fails closed (raises ``GovernancePolicyError``
or ``GovernanceProviderError``) rather than silently falling back, per the
"FAIL CLOSED" requirements for Phase 5.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from app.config.settings import Settings
from app.governance.governance_events import new_governance_event
from app.governance.governance_models import (
    GovernanceProvider,
    GovernanceProviderError,
    LocalGovernanceTraceProvider,
)
from app.models.approval_models import ApprovalAuditRecord
from app.models.governance_event import GovernanceEvent, GovernanceEventCategory
from app.repositories.governance_event_repository import (
    GovernanceEventRepository,
    InMemoryGovernanceEventRepository,
)
from app.utils.yaml_loader import YamlLoadError, load_yaml_file

_DEFAULT_POLICY_FILENAME = "governance_policy.yaml"

__all__ = [
    "AgentExecutionGovernancePolicy",
    "DecisionLineagePolicy",
    "GovernancePolicyDocument",
    "GovernancePolicyError",
    "GovernanceService",
    "SessionReplayPolicy",
    "create_governance_service",
    "load_governance_policy",
]


class GovernancePolicyError(RuntimeError):
    """Raised when the governance policy configuration is missing or invalid."""


class AgentExecutionGovernancePolicy(BaseModel):
    """Which categories of agent execution activity must be tracked.

    See "AGENT EXECUTION GOVERNANCE" in the Phase 5 instructions - every
    field here corresponds one-to-one with a tracked category.
    """

    model_config = ConfigDict(extra="forbid")

    track_registration: bool = True
    track_versions: bool = True
    track_lifecycle: bool = True
    track_executions: bool = True
    track_communication: bool = True
    track_memory_reads: bool = True
    track_memory_writes: bool = True
    track_tool_requests: bool = True
    track_policy_evaluations: bool = True
    track_denied_access: bool = True


class DecisionLineagePolicy(BaseModel):
    """Policy governing decision/recommendation lineage recording."""

    model_config = ConfigDict(extra="forbid")

    require_evidence_references: bool = True


class SessionReplayPolicy(BaseModel):
    """Policy governing whether session replay reconstruction is enabled."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True


class GovernancePolicyDocument(BaseModel):
    """The full, required governance policy document."""

    model_config = ConfigDict(extra="forbid")

    agent_execution_governance: AgentExecutionGovernancePolicy
    decision_lineage: DecisionLineagePolicy
    session_replay: SessionReplayPolicy


def load_governance_policy(
    policies_path: Path, filename: str = _DEFAULT_POLICY_FILENAME
) -> GovernancePolicyDocument:
    """Load and validate the governance policy file. Fails closed on any problem."""

    path = policies_path / filename
    if not path.is_file():
        raise GovernancePolicyError(f"Governance policy file '{path}' does not exist.")

    try:
        raw = load_yaml_file(path)
    except YamlLoadError as exc:
        raise GovernancePolicyError(str(exc)) from exc

    if not isinstance(raw, dict):
        raise GovernancePolicyError(f"Governance policy file '{path}' must define a top-level mapping.")

    try:
        return GovernancePolicyDocument.model_validate(raw)
    except ValidationError as exc:
        raise GovernancePolicyError(f"Invalid governance policy in '{path}': {exc}") from exc


class GovernanceService:
    """Records and queries governance events for a policy-driven, provider-backed audit trail."""

    def __init__(
        self,
        *,
        provider: GovernanceProvider,
        event_repository: GovernanceEventRepository,
        policy: GovernancePolicyDocument,
    ) -> None:
        self._provider = provider
        self._event_repository = event_repository
        self._policy = policy

    @property
    def policy(self) -> GovernancePolicyDocument:
        return self._policy

    async def events_for_session(self, session_id: str) -> list[GovernanceEvent]:
        return await self._event_repository.list_for_session(session_id=session_id)

    async def record_approval_audit(self, record: ApprovalAuditRecord) -> None:
        self._provider.record_approval_audit(record)

    async def _record(
        self,
        category: GovernanceEventCategory,
        *,
        session_id: str,
        trace_id: str,
        agent_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> GovernanceEvent:
        event = new_governance_event(
            category, session_id=session_id, trace_id=trace_id, agent_id=agent_id, detail=detail
        )
        await self._event_repository.append(event)
        self._provider.record(event)
        return event

    async def record_agent_registration(
        self, *, session_id: str, trace_id: str, agent_id: str, detail: dict[str, Any] | None = None
    ) -> GovernanceEvent:
        return await self._record(
            "agent_registration", session_id=session_id, trace_id=trace_id, agent_id=agent_id, detail=detail
        )

    async def record_agent_version(
        self, *, session_id: str, trace_id: str, agent_id: str, version: str
    ) -> GovernanceEvent:
        return await self._record(
            "agent_version",
            session_id=session_id,
            trace_id=trace_id,
            agent_id=agent_id,
            detail={"version": version},
        )

    async def record_lifecycle_event(
        self, *, session_id: str, trace_id: str, agent_id: str, state: str
    ) -> GovernanceEvent:
        return await self._record(
            "agent_lifecycle",
            session_id=session_id,
            trace_id=trace_id,
            agent_id=agent_id,
            detail={"state": state},
        )

    async def record_execution(
        self, *, session_id: str, trace_id: str, agent_id: str, detail: dict[str, Any] | None = None
    ) -> GovernanceEvent:
        return await self._record(
            "agent_execution", session_id=session_id, trace_id=trace_id, agent_id=agent_id, detail=detail
        )

    async def record_communication(
        self,
        *,
        session_id: str,
        trace_id: str,
        agent_id: str,
        target_agent_id: str,
        detail: dict[str, Any] | None = None,
    ) -> GovernanceEvent:
        merged_detail = {"target_agent_id": target_agent_id, **(detail or {})}
        return await self._record(
            "agent_communication",
            session_id=session_id,
            trace_id=trace_id,
            agent_id=agent_id,
            detail=merged_detail,
        )

    async def record_memory_read(
        self, *, session_id: str, trace_id: str, agent_id: str, detail: dict[str, Any] | None = None
    ) -> GovernanceEvent:
        return await self._record(
            "memory_read", session_id=session_id, trace_id=trace_id, agent_id=agent_id, detail=detail
        )

    async def record_memory_write(
        self, *, session_id: str, trace_id: str, agent_id: str, detail: dict[str, Any] | None = None
    ) -> GovernanceEvent:
        return await self._record(
            "memory_write", session_id=session_id, trace_id=trace_id, agent_id=agent_id, detail=detail
        )

    async def record_tool_request(
        self,
        *,
        session_id: str,
        trace_id: str,
        agent_id: str,
        tool_name: str,
        detail: dict[str, Any] | None = None,
    ) -> GovernanceEvent:
        merged_detail = {"tool_name": tool_name, **(detail or {})}
        return await self._record(
            "tool_request", session_id=session_id, trace_id=trace_id, agent_id=agent_id, detail=merged_detail
        )

    async def record_policy_evaluation(
        self,
        *,
        session_id: str,
        trace_id: str,
        agent_id: str | None = None,
        policy_name: str,
        allowed: bool,
        detail: dict[str, Any] | None = None,
    ) -> GovernanceEvent:
        merged_detail = {"policy_name": policy_name, "allowed": allowed, **(detail or {})}
        return await self._record(
            "policy_evaluation",
            session_id=session_id,
            trace_id=trace_id,
            agent_id=agent_id,
            detail=merged_detail,
        )

    async def record_access_denied(
        self,
        *,
        session_id: str,
        trace_id: str,
        agent_id: str | None = None,
        reason: str,
        detail: dict[str, Any] | None = None,
    ) -> GovernanceEvent:
        merged_detail = {"reason": reason, **(detail or {})}
        return await self._record(
            "access_denied", session_id=session_id, trace_id=trace_id, agent_id=agent_id, detail=merged_detail
        )

    async def record_human_checkpoint_confirmation(
        self,
        *,
        session_id: str,
        trace_id: str,
        stage_key: str,
        stage_label: str,
        confirmed_by: str,
    ) -> GovernanceEvent:
        """Record that a person explicitly proceeded the Discovery Wizard past a stage.

        Responsible AI Accountability checkpoint: the Discovery Wizard never
        auto-advances - ``confirmed_by`` is always the authenticated caller's
        user id (never a client-supplied value), so every advancement is
        attributable to a specific person.
        """

        return await self._record(
            "human_checkpoint_confirmation",
            session_id=session_id,
            trace_id=trace_id,
            agent_id=None,
            detail={"stage_key": stage_key, "stage_label": stage_label, "confirmed_by": confirmed_by},
        )

def create_governance_service(
    *,
    settings: Settings,
    provider: GovernanceProvider | None = None,
    event_repository: GovernanceEventRepository | None = None,
) -> GovernanceService:
    """Build a ``GovernanceService`` wired to the externally configured governance policy.

    Raises ``GovernancePolicyError`` (fail closed) if the governance policy
    configuration is missing or invalid.

    Provider resolution mirrors ``create_agent_gateway``'s fail-closed
    contract: production always requires an explicitly supplied ``provider``
    implementing ``Agent365GovernanceProvider`` (Genie has no built-in
    Agent365 SDK integration); ``create_governance_service`` never falls
    back to ``LocalGovernanceTraceProvider`` in production, raising
    ``GovernanceProviderError`` if none is supplied. Local/dev defaults to
    ``LocalGovernanceTraceProvider`` when no provider is supplied.
    """

    policy = load_governance_policy(settings.policies_path)

    if settings.provider_mode == "production":
        if provider is None:
            raise GovernanceProviderError(
                "No governance provider was supplied in production mode. Genie "
                "has no built-in Agent365 SDK integration; a concrete "
                "Agent365GovernanceProvider implementation must be injected "
                "via create_governance_service(provider=...). "
                "LocalGovernanceTraceProvider must never be used in production."
            )
        resolved_provider = provider
    else:
        resolved_provider = provider or LocalGovernanceTraceProvider()

    resolved_repository = event_repository or InMemoryGovernanceEventRepository()

    return GovernanceService(provider=resolved_provider, event_repository=resolved_repository, policy=policy)
