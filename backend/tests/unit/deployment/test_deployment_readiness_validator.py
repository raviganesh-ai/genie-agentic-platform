"""Unit tests for DeploymentReadinessValidator."""
from __future__ import annotations

import pytest

from app.deployment.deployment_readiness_validator import (
    DeploymentReadinessError,
    DeploymentReadinessValidator,
)
from app.deployment.models import RegistrationState, ResourceProviderRequirement
from app.deployment.provider_status_source import ResourceProviderStatusError


class _FakeStatusSource:
    """Test double for ResourceProviderStatusSource with a fixed state map."""

    def __init__(self, states: dict[str, RegistrationState]) -> None:
        self._states = states

    def get_registration_state(self, subscription_id: str, namespace: str) -> RegistrationState:
        return self._states.get(namespace, "Unknown")


class _RaisingStatusSource:
    def get_registration_state(self, subscription_id: str, namespace: str) -> RegistrationState:
        raise ResourceProviderStatusError("no credentials available")


def _requirements() -> list[ResourceProviderRequirement]:
    return [
        ResourceProviderRequirement(
            namespace="Microsoft.Storage", display_name="Azure Storage", reason="ingestion"
        ),
        ResourceProviderRequirement(
            namespace="Microsoft.KeyVault", display_name="Azure Key Vault", reason="secrets"
        ),
    ]


def test_constructor_rejects_empty_requirements():
    with pytest.raises(ValueError, match="requirements must not be empty"):
        DeploymentReadinessValidator(status_source=_FakeStatusSource({}), requirements=[])


def test_check_reports_ready_when_all_providers_registered():
    validator = DeploymentReadinessValidator(
        status_source=_FakeStatusSource(
            {"Microsoft.Storage": "Registered", "Microsoft.KeyVault": "Registered"}
        ),
        requirements=_requirements(),
    )

    report = validator.check("sub-123")

    assert report.is_ready
    assert report.blocking_statuses == []
    assert {s.registration_state for s in report.statuses} == {"Registered"}


def test_check_reports_not_ready_when_a_required_provider_is_missing():
    validator = DeploymentReadinessValidator(
        status_source=_FakeStatusSource(
            {"Microsoft.Storage": "NotRegistered", "Microsoft.KeyVault": "Registered"}
        ),
        requirements=_requirements(),
    )

    report = validator.check("sub-123")

    assert not report.is_ready
    assert [s.namespace for s in report.blocking_statuses] == ["Microsoft.Storage"]


def test_check_or_raise_raises_deployment_readiness_error_when_not_ready():
    validator = DeploymentReadinessValidator(
        status_source=_FakeStatusSource({"Microsoft.Storage": "NotRegistered"}),
        requirements=_requirements(),
    )

    with pytest.raises(DeploymentReadinessError) as exc_info:
        validator.check_or_raise("sub-123")

    assert "Microsoft.Storage" in str(exc_info.value)
    assert exc_info.value.report.subscription_id == "sub-123"


def test_check_or_raise_returns_report_when_ready():
    validator = DeploymentReadinessValidator(
        status_source=_FakeStatusSource(
            {"Microsoft.Storage": "Registered", "Microsoft.KeyVault": "Registered"}
        ),
        requirements=_requirements(),
    )

    report = validator.check_or_raise("sub-123")

    assert report.is_ready


def test_check_rejects_blank_subscription_id():
    validator = DeploymentReadinessValidator(
        status_source=_FakeStatusSource({}), requirements=_requirements()
    )

    with pytest.raises(ResourceProviderStatusError, match="subscription_id must not be blank"):
        validator.check("   ")


def test_check_propagates_status_source_errors():
    validator = DeploymentReadinessValidator(
        status_source=_RaisingStatusSource(), requirements=_requirements()
    )

    with pytest.raises(ResourceProviderStatusError, match="no credentials available"):
        validator.check("sub-123")
