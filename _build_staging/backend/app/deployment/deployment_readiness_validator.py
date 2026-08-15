"""Deployment-readiness validation: run before any infrastructure is provisioned.

This is deliberately NOT part of ``app.validation`` / the fixed 10-validator
``StartupValidationRunner`` used by the FastAPI app's own lifespan (per the
Validation Requirements in ``.github/copilot-instructions.md``, that list
must not change). Deployment readiness is checked against an Azure
*subscription* before Genie's infrastructure - and therefore the running
application itself - exists at all, so it is invoked directly by deployment
tooling (``scripts/validate_deployment_readiness.py``), never by the app.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.deployment.models import (
    DeploymentReadinessReport,
    ResourceProviderRequirement,
    ResourceProviderStatus,
)
from app.deployment.provider_status_source import (
    ResourceProviderStatusError,
    ResourceProviderStatusSource,
)


class DeploymentReadinessError(RuntimeError):
    """Raised when one or more required Azure resource providers are not registered.

    Deployment tooling must not proceed to provision infrastructure when
    this is raised; no fallback behavior is implemented, per the Fail
    Closed Requirements in ``.github/copilot-instructions.md``.
    """

    def __init__(self, report: DeploymentReadinessReport) -> None:
        self.report = report
        messages = [
            f"[{status.namespace}] {status.display_name} is '{status.registration_state}' "
            "(required: Registered)."
            for status in report.blocking_statuses
        ]
        super().__init__(
            f"Deployment readiness check failed for subscription '{report.subscription_id}':\n"
            + "\n".join(messages)
        )


class DeploymentReadinessValidator:
    """Checks whether every required Azure resource provider is registered."""

    def __init__(
        self,
        *,
        status_source: ResourceProviderStatusSource,
        requirements: list[ResourceProviderRequirement],
    ) -> None:
        if not requirements:
            raise ValueError("requirements must not be empty.")
        self._status_source = status_source
        self._requirements = requirements

    def check(self, subscription_id: str) -> DeploymentReadinessReport:
        """Return a ``DeploymentReadinessReport`` for ``subscription_id``.

        Never raises on a not-registered provider - callers decide whether
        to fail closed via ``check_or_raise``. Raises
        ``ResourceProviderStatusError`` only if the status lookup itself
        fails (e.g. no credentials, no network).
        """

        if not subscription_id.strip():
            raise ResourceProviderStatusError("subscription_id must not be blank.")

        statuses: list[ResourceProviderStatus] = []
        for requirement in self._requirements:
            state = self._status_source.get_registration_state(
                subscription_id, requirement.namespace
            )
            statuses.append(
                ResourceProviderStatus(
                    namespace=requirement.namespace,
                    display_name=requirement.display_name,
                    required=requirement.required,
                    registration_state=state,
                )
            )

        return DeploymentReadinessReport(
            subscription_id=subscription_id,
            checked_at=datetime.now(UTC),
            statuses=statuses,
        )

    def check_or_raise(self, subscription_id: str) -> DeploymentReadinessReport:
        """Run ``check`` and raise ``DeploymentReadinessError`` if not ready."""

        report = self.check(subscription_id)
        if not report.is_ready:
            raise DeploymentReadinessError(report)
        return report
