"""Foundry agent drift validator (Phase 10A).

Two complementary responsibilities:

1. ``validate(settings)`` - a fail-closed, configuration-only (no network,
   no prior run state) ``StartupValidator`` that catches *structural*
   drift detectable from configuration alone: a workflow step referencing
   a disabled agent, or a workflow step's ``prompt_id`` diverging from its
   agent's own configured ``prompt_template_ref``. Like
   ``FoundryAgentRegistryValidator``, this is a no-op outside production
   and is invoked separately from ``app.main``'s lifespan - never inserted
   into the fixed ``StartupValidationRunner.default_validators()`` list.

2. ``detect_drift(agent, previous)`` - stateful, point-in-time comparison
   of an agent's *current* configuration against the last
   ``FoundryAgentInventoryRecord`` observed for it, used by
   ``FoundryAgentSynchronizationService`` and the Foundry admin API to
   produce a ``DriftReport`` (e.g. "the configured owner changed since the
   last synchronization run").
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.agents.models import AgentDefinition
from app.agents.registry import AgentRegistry, AgentRegistryError
from app.config.settings import Settings
from app.models.drift_report import DriftIssue, DriftReport
from app.models.foundry_agent_inventory_record import FoundryAgentInventoryRecord
from app.validation.base import ValidationResult
from app.workflows.registry import WorkflowRegistry, WorkflowRegistryError

__all__ = ["FoundryAgentDriftValidator"]


class FoundryAgentDriftValidator:
    """Fails closed in production on structural config drift; also computes ``DriftReport``s."""

    name = "FoundryAgentDriftValidator"

    def validate(self, settings: Settings) -> ValidationResult:
        if settings.provider_mode != "production":
            return ValidationResult.ok(self.name)

        try:
            agent_registry = AgentRegistry.load(settings.agents_path, default_llm=settings.default_llm)
            workflow_registry = WorkflowRegistry.load(settings.workflows_path)
        except (AgentRegistryError, WorkflowRegistryError) as exc:
            return ValidationResult.fail(self.name, [str(exc)])

        errors: list[str] = []
        for workflow in workflow_registry.list():
            for step in workflow.steps:
                if step.agent_id not in agent_registry:
                    # Already reported by WorkflowRegistry.validate_agent_references.
                    continue
                agent = agent_registry.get(step.agent_id)
                if not agent.enabled:
                    errors.append(
                        f"Lifecycle drift: workflow '{workflow.id}' step '{step.id}' "
                        f"references disabled agent '{agent.id}'."
                    )
                if (
                    step.prompt_id
                    and agent.prompt_template_ref
                    and step.prompt_id != agent.prompt_template_ref
                ):
                    errors.append(
                        f"Prompt drift: workflow '{workflow.id}' step '{step.id}' uses "
                        f"prompt '{step.prompt_id}' but agent '{agent.id}' is configured "
                        f"for '{agent.prompt_template_ref}'."
                    )

        if errors:
            return ValidationResult.fail(self.name, errors)
        return ValidationResult.ok(self.name)

    def detect_drift(
        self, agent: AgentDefinition, previous: FoundryAgentInventoryRecord | None
    ) -> DriftReport:
        """Compare ``agent``'s current configuration against ``previous`` inventory state.

        Returns an empty ``DriftReport`` (no issues) if ``previous`` is
        ``None`` - there is nothing to compare a never-before-seen agent
        against.
        """

        now = datetime.now(UTC)
        if previous is None:
            return DriftReport(agent_id=agent.id, issues=[], detected_at=now)

        issues: list[DriftIssue] = []

        if agent.version != previous.version:
            issues.append(
                DriftIssue(
                    agent_id=agent.id,
                    issue_type="version_mismatch",
                    expected_value=previous.version,
                    actual_value=agent.version,
                    severity="medium",
                    recommendation="Confirm the version change was an intentional deployment.",
                )
            )

        if (agent.model_deployment_ref or "") != (previous.model_deployment_ref or ""):
            issues.append(
                DriftIssue(
                    agent_id=agent.id,
                    issue_type="deployment_mismatch",
                    expected_value=previous.model_deployment_ref or "",
                    actual_value=agent.model_deployment_ref or "",
                    severity="high",
                    recommendation="Verify the new deployment reference is provisioned in Foundry.",
                )
            )

        if (agent.governance_policy_id or "") != (previous.governance_policy_id or ""):
            issues.append(
                DriftIssue(
                    agent_id=agent.id,
                    issue_type="governance_policy_mismatch",
                    expected_value=previous.governance_policy_id or "",
                    actual_value=agent.governance_policy_id or "",
                    severity="critical",
                    recommendation="Governance policy changes require review before re-provisioning.",
                )
            )

        if (agent.prompt_template_ref or "") != (previous.prompt_template_ref or ""):
            issues.append(
                DriftIssue(
                    agent_id=agent.id,
                    issue_type="prompt_mismatch",
                    expected_value=previous.prompt_template_ref or "",
                    actual_value=agent.prompt_template_ref or "",
                    severity="medium",
                    recommendation="Confirm the new prompt template has been reviewed and approved.",
                )
            )

        if (agent.owner or "") != (previous.owner or ""):
            issues.append(
                DriftIssue(
                    agent_id=agent.id,
                    issue_type="ownership_mismatch",
                    expected_value=previous.owner or "",
                    actual_value=agent.owner or "",
                    severity="low",
                    recommendation="Confirm ownership transfer was authorized.",
                )
            )

        if sorted(agent.memory_access) != sorted(previous.memory_scope):
            issues.append(
                DriftIssue(
                    agent_id=agent.id,
                    issue_type="memory_configuration_mismatch",
                    expected_value=",".join(sorted(previous.memory_scope)),
                    actual_value=",".join(sorted(agent.memory_access)),
                    severity="high",
                    recommendation="Review memory access policy implications before re-provisioning.",
                )
            )

        if agent.enabled and previous.lifecycle_state in ("deprecated", "retired"):
            issues.append(
                DriftIssue(
                    agent_id=agent.id,
                    issue_type="lifecycle_mismatch",
                    expected_value=previous.lifecycle_state,
                    actual_value="enabled",
                    severity="critical",
                    recommendation=(
                        "Agent is enabled in configuration but its last known lifecycle "
                        "state is deprecated/retired; re-provision explicitly before enabling."
                    ),
                )
            )

        return DriftReport(agent_id=agent.id, issues=issues, detected_at=now)
