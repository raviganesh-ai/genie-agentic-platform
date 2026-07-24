"""CLI: render an Azure custom RBAC role definition for Genie deployments.

Usage (from the repo root)::

    python scripts/generate_deployment_role_definition.py --subscription-id <id>

Prints a role-definition JSON document to stdout, scoped to *exactly* the
Azure resource provider namespaces Genie's infra/ depends on (the same
``config/deployment/resource_providers.yaml`` catalog used by
``validate_deployment_readiness.py``) - never the built-in Owner or
Contributor role, per the Security Requirements ("least privilege") in
``.github/copilot-instructions.md``.

Intended to be piped into a temp file and passed to
``az role definition create --role-definition <file>`` (see
``scripts/create_deployment_identity.ps1``).
"""
from __future__ import annotations

import argparse
import json
import sys

from app.deployment.resource_provider_requirements import (
    ResourceProviderRequirementError,
    load_resource_provider_requirements,
)
from app.deployment.settings import get_deployment_settings

DEFAULT_ROLE_NAME = "Genie Infrastructure Deployer"


def build_role_definition(subscription_id: str, role_name: str, requirements) -> dict:
    actions = sorted({f"{requirement.namespace}/*" for requirement in requirements})
    return {
        "Name": role_name,
        "IsCustom": True,
        "Description": (
            "Least-privilege deployment identity for provisioning Genie's Azure "
            "infrastructure. Scoped to exactly the resource provider namespaces "
            "declared in config/deployment/resource_providers.yaml - deliberately "
            "narrower than Owner or Contributor."
        ),
        "Actions": actions,
        "NotActions": [],
        "DataActions": [],
        "NotDataActions": [],
        "AssignableScopes": [f"/subscriptions/{subscription_id}"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--subscription-id",
        default=None,
        help="Azure subscription id. Defaults to GENIE_DEPLOY_SUBSCRIPTION_ID.",
    )
    parser.add_argument("--role-name", default=DEFAULT_ROLE_NAME)
    args = parser.parse_args()

    settings = get_deployment_settings()
    subscription_id = args.subscription_id or settings.subscription_id
    if not subscription_id or not subscription_id.strip():
        print(
            "ERROR: no subscription id supplied (--subscription-id or "
            "GENIE_DEPLOY_SUBSCRIPTION_ID).",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        requirements = load_resource_provider_requirements(settings.resource_providers_config_dir)
    except ResourceProviderRequirementError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    role_definition = build_role_definition(subscription_id, args.role_name, requirements)
    print(json.dumps(role_definition, indent=2))


if __name__ == "__main__":
    main()
