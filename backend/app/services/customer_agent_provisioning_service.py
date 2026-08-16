"""Per-customer dedicated Foundry agent provisioning.

Implements the "each customer gets their own agents" capability: on
demand, ``CustomerAgentProvisioningService`` clones every enabled,
already-approved catalog agent (``config/agents/*.yaml``) into a brand new,
dedicated Azure AI Foundry agent resource for that one session - never
inventing new agent reasoning, since the cloned instructions are always
exactly the catalog agent's own configured ``description``.

Once provisioned, ``AzureAgentGateway`` (see ``app.agents.gateway``'s
``SessionAgentResolver`` seam) automatically routes every further
execution for that session - workflow resumes, chat, reanalysis routing -
to the session's own dedicated agents instead of the shared catalog pool,
with no change required in any orchestration/execution code.

Dedicated agents are torn down only on an explicit session close (see
``POST /sessions/{id}/cx-access/close``), never on an idle timeout - a
customer's link may remain a live conversation for as long as staff choose
to keep it open.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from app.agents.foundry.errors import FoundryUnavailableError
from app.agents.foundry.project_service import FoundryProjectService
from app.agents.registry import AgentRegistry
from app.config.settings import Settings
from app.governance.governance_service import GovernanceService

__all__ = [
    "CustomerAgentProvisioningError",
    "CustomerAgentProvisioningService",
    "NullCustomerAgentProvisioningService",
    "ProvisionedAgentRecord",
    "create_customer_agent_provisioning_service",
]


class CustomerAgentProvisioningError(RuntimeError):
    """Raised when provisioning/deprovisioning a customer's dedicated agents fails."""


_FOUNDRY_AGENT_NAME_MAX_LENGTH = 63
_INVALID_FOUNDRY_AGENT_NAME_CHARS = re.compile(r"[^A-Za-z0-9-]+")


def _dedicated_agent_name(*, agent_id: str, scope_key: str) -> str:
    suffix = _INVALID_FOUNDRY_AGENT_NAME_CHARS.sub("-", scope_key).strip("-").lower()
    if not suffix:
        suffix = "scope"
    name = f"{agent_id}-cx-{suffix}"
    name = name[:_FOUNDRY_AGENT_NAME_MAX_LENGTH].rstrip("-")
    if not name or not name[-1].isalnum():
        name = f"{name.rstrip('-')}0"
    return name


@dataclass(frozen=True)
class ProvisionedAgentRecord:
    """One catalog agent's dedicated Foundry resource, cloned for a single session."""

    agent_id: str
    foundry_agent_id: str
    model_deployment_ref: str
    provisioned_at: datetime


class CustomerAgentProvisioningService:
    """Creates and tears down a dedicated Foundry agent fleet per customer session.

    State (a scope key -> {agent_id: dedicated foundry_agent_id} mapping) is
    held in-process, the same precedent already established by
    ``DecisionGraphService`` / ``FoundryAgentInventoryService`` - a durable
    backend can replace this later without changing any caller.

    Every method accepts an optional ``scope_id`` that, when supplied,
    replaces ``session_id`` as the fleet's storage key - this lets a caller
    provision a fleet dedicated to something narrower than a whole session
    (e.g. one requirement group), so distinct requirement groups within the
    same session never share dedicated agents. ``session_id`` is always
    still required (and used whenever ``scope_id`` is omitted, and always
    for governance lifecycle event attribution).
    """

    def __init__(
        self,
        *,
        agent_registry: AgentRegistry,
        project_service: FoundryProjectService,
        governance_service: GovernanceService,
    ) -> None:
        self._agent_registry = agent_registry
        self._project_service = project_service
        self._governance_service = governance_service
        self._provisioned: dict[str, dict[str, ProvisionedAgentRecord]] = {}

    def is_provisioned(self, session_id: str, *, scope_id: str | None = None) -> bool:
        return bool(self._provisioned.get(scope_id or session_id))

    def resolve(
        self, *, session_id: str, agent_id: str, scope_id: str | None = None
    ) -> str | None:
        """``SessionAgentResolver`` implementation consulted by ``AzureAgentGateway``."""

        record = self._provisioned.get(scope_id or session_id, {}).get(agent_id)
        return record.foundry_agent_id if record else None

    def provisioned_agents(
        self, session_id: str, *, scope_id: str | None = None
    ) -> list[ProvisionedAgentRecord]:
        return list(self._provisioned.get(scope_id or session_id, {}).values())

    async def provision_for_session(
        self,
        *,
        session_id: str,
        scope_id: str | None = None,
        trace_id: str | None = None,
        model_deployment_ref: str | None = None,
    ) -> list[ProvisionedAgentRecord]:
        """Provision a dedicated Foundry agent for every enabled catalog agent.

        Idempotent: if the resolved scope key (``scope_id`` or ``session_id``)
        already has dedicated agents, they are returned unchanged rather than
        re-created. Fails closed: if any agent's ``create_agent`` call fails,
        every agent already created for this scope in this call is rolled
        back before the error is raised - a scope never ends up with a
        partially provisioned fleet.
        """

        key = scope_id or session_id
        existing = self._provisioned.get(key)
        if existing:
            return list(existing.values())

        resolved_trace_id = trace_id or str(uuid4())
        try:
            client = self._project_service.get_api_client()
        except FoundryUnavailableError as exc:
            raise CustomerAgentProvisioningError(
                f"Cannot provision dedicated customer agents: {exc}"
            ) from exc

        records: dict[str, ProvisionedAgentRecord] = {}
        try:
            for agent in self._agent_registry.list():
                if not agent.enabled or not agent.foundry_agent_id:
                    continue
                effective_model = (model_deployment_ref or agent.model_deployment_ref or "").strip()
                if not effective_model:
                    raise CustomerAgentProvisioningError(
                        f"Cannot provision dedicated agent '{agent.id}': no model deployment ref resolved."
                    )
                dedicated_foundry_agent_id = client.create_agent(
                    name=_dedicated_agent_name(agent_id=agent.id, scope_key=key),
                    model=effective_model,
                    instructions=agent.description,
                    description=agent.name,
                )
                records[agent.id] = ProvisionedAgentRecord(
                    agent_id=agent.id,
                    foundry_agent_id=dedicated_foundry_agent_id,
                    model_deployment_ref=effective_model,
                    provisioned_at=datetime.now(UTC),
                )
                await self._governance_service.record_lifecycle_event(
                    session_id=session_id,
                    trace_id=resolved_trace_id,
                    agent_id=agent.id,
                    state="provisioned",
                )
        except Exception as exc:
            for record in records.values():
                self._safe_delete(record.foundry_agent_id)
            raise CustomerAgentProvisioningError(
                f"Failed to provision dedicated agents for scope '{key}': {exc}"
            ) from exc

        self._provisioned[key] = records
        return list(records.values())

    async def deprovision_for_session(
        self, *, session_id: str, scope_id: str | None = None, trace_id: str | None = None
    ) -> None:
        """Delete every dedicated Foundry agent provisioned for this scope.

        A no-op if nothing was ever provisioned for this scope. Only ever
        called on an explicit session/group close - never on an idle timeout.
        """

        records = self._provisioned.pop(scope_id or session_id, None)
        if not records:
            return

        resolved_trace_id = trace_id or str(uuid4())
        client = self._project_service.get_api_client()
        for record in records.values():
            client.delete_agent(record.foundry_agent_id)
            await self._governance_service.record_lifecycle_event(
                session_id=session_id,
                trace_id=resolved_trace_id,
                agent_id=record.agent_id,
                state="retired",
            )

    def _safe_delete(self, foundry_agent_id: str) -> None:
        try:
            self._project_service.get_api_client().delete_agent(foundry_agent_id)
        except Exception:  # noqa: BLE001, S110 - best-effort cleanup during rollback;
            # the original provisioning failure is already the exception raised to the
            # caller, so a secondary cleanup failure here must never mask or replace it.
            pass


class NullCustomerAgentProvisioningService:
    """No-op stand-in used when per-customer provisioning is not configured.

    Local/dev only (mirrors ``LocalAgentGateway``): every session resolves
    to ``None`` (falling back to the shared, statically configured
    ``foundry_agent_id`` pool), and provisioning/deprovisioning calls are
    accepted but perform no real Foundry action - so callers never need to
    branch on whether per-customer provisioning is actually configured.
    """

    def is_provisioned(self, session_id: str, *, scope_id: str | None = None) -> bool:
        return False

    def resolve(
        self, *, session_id: str, agent_id: str, scope_id: str | None = None
    ) -> str | None:
        return None

    def provisioned_agents(
        self, session_id: str, *, scope_id: str | None = None
    ) -> list[ProvisionedAgentRecord]:
        return []

    async def provision_for_session(
        self,
        *,
        session_id: str,
        scope_id: str | None = None,
        trace_id: str | None = None,
        model_deployment_ref: str | None = None,
    ) -> list[ProvisionedAgentRecord]:
        return []

    async def deprovision_for_session(
        self, *, session_id: str, scope_id: str | None = None, trace_id: str | None = None
    ) -> None:
        return None


def create_customer_agent_provisioning_service(
    *,
    settings: Settings,
    agent_registry: AgentRegistry,
    governance_service: GovernanceService,
) -> CustomerAgentProvisioningService | NullCustomerAgentProvisioningService:
    """Fail-closed factory mirroring ``create_agent_gateway``.

    Uses the null implementation unless Azure AI Foundry happens to be
    configured, in which case the real service is used (so local
    development can still exercise the real provisioning path against a
    real dev Foundry project).
    """

    def _build_real() -> CustomerAgentProvisioningService:
        if not settings.azure_foundry_endpoint or not settings.azure_foundry_project_name:
            raise CustomerAgentProvisioningError(
                "azure_foundry_endpoint and azure_foundry_project_name must be "
                "configured to provision dedicated per-customer Foundry agents."
            )
        project_service = FoundryProjectService(
            endpoint=settings.azure_foundry_endpoint,
            project_name=settings.azure_foundry_project_name,
        )
        return CustomerAgentProvisioningService(
            agent_registry=agent_registry,
            project_service=project_service,
            governance_service=governance_service,
        )

    if settings.azure_foundry_endpoint and settings.azure_foundry_project_name:
        return _build_real()

    return NullCustomerAgentProvisioningService()
