"""Rich, per-agent Foundry agent synchronization service (Phase 10A).

Distinct from (and layered above) the simpler, fail-closed
``app.agents.foundry.agent_synchronization_service.FoundryAgentSynchronizationService``
introduced in Phase 3 - that service remains the all-or-nothing gate run
directly in ``app.main``'s lifespan before the app becomes ready. This
service instead produces one structured ``SynchronizationResult`` per
configured agent (never raising on an individual agent's failure), so the
Foundry admin API, inventory, and CLI tooling can observe *which* agents
are healthy and *why* others are not, per the 9-step synchronization
procedure:

    1. Load configuration (``AgentDefinition`` from ``AgentRegistry``).
    2. Validate required metadata is present.
    3. Verify the Foundry agent resource exists (network check via the
       approved ``FoundryProjectService`` / ``AgentApiClient`` access
       layer only - never a direct SDK call).
    4. Verify agent version metadata.
    5. Verify the prompt template reference resolves in ``PromptRegistry``.
    6. Verify governance metadata is present.
    7. Verify the deployment reference is present.
    8. Verify ownership metadata is present.
    9. Verify memory scope configuration is present.

Every step's outcome is folded into one ``SynchronizationResult`` per
agent and recorded into ``FoundryAgentInventoryService``.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.agents.foundry.errors import FoundryUnavailableError
from app.agents.foundry.project_service import FoundryProjectService
from app.agents.models import AgentDefinition
from app.agents.registry import AgentRegistry
from app.models.drift_report import DriftReport
from app.models.synchronization_result import SynchronizationReport, SynchronizationResult
from app.prompts.registry import PromptRegistry
from app.services.foundry_agent_inventory_service import (
    FoundryAgentInventoryService,
    UnknownInventoryAgentError,
)
from app.validation.foundry_agent_drift_validator import FoundryAgentDriftValidator

__all__ = ["FoundryAgentSynchronizationService"]


def _validate_static_metadata(agent: AgentDefinition, prompt_registry: PromptRegistry) -> list[str]:
    """Steps 2, 4-9: metadata-completeness checks that require no network access."""

    issues: list[str] = []
    if not agent.version.strip():
        issues.append("agent version is missing.")
    if not agent.prompt_template_ref:
        issues.append("promptTemplateRef is missing.")
    elif agent.prompt_template_ref not in prompt_registry:
        issues.append(f"promptTemplateRef '{agent.prompt_template_ref}' does not exist.")
    if not agent.governance_policy_id:
        issues.append("governancePolicyId is missing.")
    if not agent.model_deployment_ref:
        issues.append("deployment reference (model_deployment_ref) is missing.")
    if not agent.owner:
        issues.append("owner metadata is missing.")
    if not agent.memory_access:
        issues.append("memory scope is empty.")
    return issues


class FoundryAgentSynchronizationService:
    """Produces a per-agent ``SynchronizationReport`` and updates the inventory."""

    def __init__(
        self,
        *,
        project_service: FoundryProjectService,
        prompt_registry: PromptRegistry,
        inventory_service: FoundryAgentInventoryService,
        drift_validator: FoundryAgentDriftValidator | None = None,
    ) -> None:
        self._project_service = project_service
        self._prompt_registry = prompt_registry
        self._inventory_service = inventory_service
        self._drift_validator = drift_validator or FoundryAgentDriftValidator()
        self._drift_reports: dict[str, DriftReport] = {}
        self._last_report: SynchronizationReport | None = None

    def drift_reports(self) -> dict[str, DriftReport]:
        """Drift reports produced by the most recent ``synchronize()`` call."""

        return dict(self._drift_reports)

    def last_report(self) -> SynchronizationReport | None:
        """The most recent ``SynchronizationReport``, or ``None`` if never run."""

        return self._last_report

    async def synchronize(self, agent_registry: AgentRegistry) -> SynchronizationReport:
        """Synchronize every enabled agent in ``agent_registry``.

        Never raises on an individual agent's failure - each agent gets its
        own ``SynchronizationResult`` recorded to the inventory. Callers
        that must fail closed on any failure should inspect
        ``SynchronizationReport.has_blocking_failures``.
        """

        results: list[SynchronizationResult] = []
        for agent in agent_registry.list():
            if not agent.enabled:
                continue

            previous_record = None
            try:
                previous_record = await self._inventory_service.get(agent.id)
            except UnknownInventoryAgentError:
                previous_record = None
            await self._inventory_service.seed_from_agent(agent)

            result = await self._synchronize_one(agent)
            if result.status == "synchronized":
                drift_report = self._drift_validator.detect_drift(agent, previous_record)
                self._drift_reports[agent.id] = drift_report
                if drift_report.issues:
                    result = SynchronizationResult(
                        agent_id=agent.id,
                        status="drift_detected",
                        issues=[issue.recommendation for issue in drift_report.issues],
                        synchronized_at=result.synchronized_at,
                    )

            results.append(result)
            await self._inventory_service.record_synchronization_result(result)

        report = SynchronizationReport(results=results, generated_at=datetime.now(UTC))
        self._last_report = report
        return report

    async def _synchronize_one(self, agent: AgentDefinition) -> SynchronizationResult:
        now = datetime.now(UTC)

        # Step 3: an agent with no configured foundry_agent_id has not yet
        # been linked to a Foundry resource - distinct from one that names a
        # resource that cannot be found.
        if not agent.foundry_agent_id:
            return SynchronizationResult(
                agent_id=agent.id,
                status="provisioning_required",
                issues=["foundryAgentReference is missing; agent has not been provisioned."],
                synchronized_at=now,
            )

        try:
            client = self._project_service.get_api_client()
            exists = client.agent_exists(agent.foundry_agent_id)
        except FoundryUnavailableError as exc:
            return SynchronizationResult(
                agent_id=agent.id,
                status="not_found",
                issues=[f"Azure AI Foundry lookup failed: {exc}"],
                synchronized_at=now,
            )

        if not exists:
            return SynchronizationResult(
                agent_id=agent.id,
                status="not_found",
                issues=[
                    f"No Foundry agent resource found for '{agent.foundry_agent_id}'."
                ],
                synchronized_at=now,
            )

        # Steps 2, 4-9.
        metadata_issues = _validate_static_metadata(agent, self._prompt_registry)
        if metadata_issues:
            return SynchronizationResult(
                agent_id=agent.id,
                status="validation_failed",
                issues=metadata_issues,
                synchronized_at=now,
            )

        return SynchronizationResult(agent_id=agent.id, status="synchronized", synchronized_at=now)
