"""CLI: verify an Azure subscription is ready before Genie's infra is provisioned.

Usage (from the ``backend`` virtual environment, invoked from the repo
root so the default ``config/deployment`` path resolves correctly)::

    python scripts/validate_deployment_readiness.py

Required environment variables:

    GENIE_DEPLOY_SUBSCRIPTION_ID    Azure subscription id to check.

Checks every Azure resource provider namespace listed in
``config/deployment/resource_providers.yaml`` (the single source of truth
shared with ``infra/``) and reports whether each is ``Registered``. Prints
a ``DeploymentReadinessReport`` as JSON and exits non-zero if any required
provider is not registered, or if the registration state cannot be
determined at all (e.g. no Azure credentials available).

Per the "Deployment readiness validation must occur before infrastructure
provisioning" requirement, this script must be run - and must exit 0 -
before ``az deployment sub create`` is invoked; see
``scripts/deploy_infra.ps1`` for the orchestrating gate.
"""
from __future__ import annotations

import sys

from app.deployment.deployment_readiness_validator import DeploymentReadinessValidator
from app.deployment.provider_status_source import (
    AzureResourceManagerProviderStatusSource,
    ResourceProviderStatusError,
)
from app.deployment.resource_provider_requirements import (
    ResourceProviderRequirementError,
    load_resource_provider_requirements,
)
from app.deployment.settings import get_deployment_settings


def main() -> None:
    settings = get_deployment_settings()

    if not settings.subscription_id or not settings.subscription_id.strip():
        print(
            "ERROR: GENIE_DEPLOY_SUBSCRIPTION_ID is not set. Deployment readiness cannot "
            "be checked without a target subscription.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        requirements = load_resource_provider_requirements(settings.resource_providers_config_dir)
    except ResourceProviderRequirementError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    validator = DeploymentReadinessValidator(
        status_source=AzureResourceManagerProviderStatusSource(),
        requirements=requirements,
    )

    try:
        report = validator.check(settings.subscription_id)
    except ResourceProviderStatusError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    print(report.model_dump_json(indent=2))

    if not report.is_ready:
        print(
            f"\n[NOT READY] {len(report.blocking_statuses)} required resource provider(s) "
            "are not registered. Register them (e.g. `az provider register --namespace "
            "<namespace>`) and re-run this check before provisioning infrastructure.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("\n[READY] All required Azure resource providers are registered.")
    sys.exit(0)


if __name__ == "__main__":
    main()
