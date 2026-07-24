"""Integration test: confirm the real config/deployment catalog is well-formed.

Mirrors the pattern in tests/integration/test_foundry_agent_catalog_config.py -
loads the real repo config/ directory (not a tmp_path fixture) to prove the
requirements Genie's infra/ depends on stay in sync with what deployment
tooling actually checks.
"""
from __future__ import annotations

from pathlib import Path

from app.deployment.deployment_readiness_validator import DeploymentReadinessValidator
from app.deployment.resource_provider_requirements import load_resource_provider_requirements

_REPO_CONFIG_ROOT = Path(__file__).resolve().parents[3] / "config"


class _AllRegisteredStatusSource:
    def get_registration_state(self, subscription_id: str, namespace: str) -> str:
        return "Registered"


class _NoneRegisteredStatusSource:
    def get_registration_state(self, subscription_id: str, namespace: str) -> str:
        return "NotRegistered"


def test_real_resource_provider_config_loads_and_covers_expected_namespaces():
    requirements = load_resource_provider_requirements(_REPO_CONFIG_ROOT / "deployment")

    namespaces = {r.namespace for r in requirements}
    for expected in (
        "Microsoft.Resources",
        "Microsoft.ManagedIdentity",
        "Microsoft.KeyVault",
        "Microsoft.Storage",
        "Microsoft.CognitiveServices",
        "Microsoft.Search",
        "Microsoft.DocumentDB",
        "Microsoft.App",
        "Microsoft.Web",
        "Microsoft.OperationalInsights",
        "Microsoft.Insights",
    ):
        assert expected in namespaces


def test_validator_passes_against_real_config_when_all_providers_registered():
    requirements = load_resource_provider_requirements(_REPO_CONFIG_ROOT / "deployment")
    validator = DeploymentReadinessValidator(
        status_source=_AllRegisteredStatusSource(), requirements=requirements
    )

    report = validator.check("00000000-0000-0000-0000-000000000000")

    assert report.is_ready


def test_validator_fails_against_real_config_when_no_providers_registered():
    requirements = load_resource_provider_requirements(_REPO_CONFIG_ROOT / "deployment")
    validator = DeploymentReadinessValidator(
        status_source=_NoneRegisteredStatusSource(), requirements=requirements
    )

    report = validator.check("00000000-0000-0000-0000-000000000000")

    assert not report.is_ready
    assert len(report.blocking_statuses) == len(requirements)
