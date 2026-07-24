"""Fail-closed tests for GovernanceProviderValidator (Phase 5)."""
from __future__ import annotations

from app.validation.governance_provider_validator import GovernanceProviderValidator


def test_passes_for_valid_local_settings(local_settings):
    assert GovernanceProviderValidator().validate(local_settings).passed


def test_passes_for_fully_configured_production_settings(production_settings):
    assert GovernanceProviderValidator().validate(production_settings).passed


def test_fails_closed_when_provider_unknown(local_settings):
    settings = local_settings.model_copy(update={"governance_provider": "not-a-real-provider"})
    result = GovernanceProviderValidator().validate(settings)
    assert not result.passed


def test_fails_closed_when_production_uses_local_provider(production_settings):
    settings = production_settings.model_copy(update={"governance_provider": "local"})
    result = GovernanceProviderValidator().validate(settings)
    assert not result.passed


def test_fails_closed_when_governance_policy_missing(local_settings):
    (local_settings.policies_path / "governance_policy.yaml").unlink()
    result = GovernanceProviderValidator().validate(local_settings)
    assert not result.passed


def test_fails_closed_when_governance_policy_missing_section(local_settings):
    (local_settings.policies_path / "governance_policy.yaml").write_text(
        "agent_execution_governance:\n  track_registration: true\n",
        encoding="utf-8",
    )
    result = GovernanceProviderValidator().validate(local_settings)
    assert not result.passed


def test_fails_closed_when_approval_policy_missing(local_settings):
    (local_settings.policies_path / "approval_policy.yaml").unlink()
    result = GovernanceProviderValidator().validate(local_settings)
    assert not result.passed


def test_fails_closed_when_approval_policy_has_no_checkpoints(local_settings):
    (local_settings.policies_path / "approval_policy.yaml").write_text(
        "default_expiry_minutes: 60\ncheckpoints: []\n",
        encoding="utf-8",
    )
    result = GovernanceProviderValidator().validate(local_settings)
    assert not result.passed


def test_fails_closed_when_production_lineage_store_is_in_memory(production_settings):
    settings = production_settings.model_copy(update={"lineage_store_backend": "in_memory"})
    result = GovernanceProviderValidator().validate(settings)
    assert not result.passed
    assert any("lineage_store_backend" in issue.message for issue in result.issues)


def test_fails_closed_when_production_lineage_store_endpoint_missing(production_settings):
    settings = production_settings.model_copy(update={"lineage_store_endpoint": None})
    result = GovernanceProviderValidator().validate(settings)
    assert not result.passed
    assert any("lineage_store_endpoint" in issue.message for issue in result.issues)
