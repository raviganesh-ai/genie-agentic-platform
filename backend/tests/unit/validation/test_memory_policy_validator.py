"""Fail-closed tests for MemoryPolicyValidator."""
from __future__ import annotations

from app.validation.memory_policy_validator import MemoryPolicyValidator


def test_passes_when_policy_file_present(local_settings):
    assert MemoryPolicyValidator().validate(local_settings).passed


def test_passes_for_fully_configured_production_settings(production_settings):
    assert MemoryPolicyValidator().validate(production_settings).passed


def test_fails_closed_when_policy_file_missing(local_settings):
    (local_settings.policies_path / "memory_policy.yaml").unlink()
    result = MemoryPolicyValidator().validate(local_settings)
    assert not result.passed


def test_fails_closed_when_shared_collaboration_section_missing(local_settings):
    (local_settings.policies_path / "memory_policy.yaml").write_text(
        "personal_agent_memory:\n  accessible_by: owning_agent\n",
        encoding="utf-8",
    )
    result = MemoryPolicyValidator().validate(local_settings)
    assert not result.passed


def test_fails_closed_when_enterprise_section_missing(local_settings):
    (local_settings.policies_path / "memory_policy.yaml").write_text(
        "personal_agent_memory:\n"
        "  accessible_by: owning_agent\n"
        "shared_collaboration_memory:\n"
        "  accessible_by: session_participants\n"
        "  emits_governance_events: true\n"
        "  require_approval_for_overwrite: true\n",
        encoding="utf-8",
    )
    result = MemoryPolicyValidator().validate(local_settings)
    assert not result.passed


def test_fails_closed_when_production_memory_store_is_in_memory(production_settings):
    settings = production_settings.model_copy(update={"memory_store_backend": "in_memory"})
    result = MemoryPolicyValidator().validate(settings)
    assert not result.passed
    assert any("memory_store_backend" in issue.message for issue in result.issues)


def test_fails_closed_when_production_memory_store_endpoint_missing(production_settings):
    settings = production_settings.model_copy(update={"memory_store_endpoint": None})
    result = MemoryPolicyValidator().validate(settings)
    assert not result.passed
    assert any("memory_store_endpoint" in issue.message for issue in result.issues)
