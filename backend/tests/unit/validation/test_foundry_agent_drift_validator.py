"""Unit tests for FoundryAgentDriftValidator."""
from __future__ import annotations

from pathlib import Path

from app.agents.models import AgentDefinition
from app.config.settings import Settings
from app.models.foundry_agent_inventory_record import FoundryAgentInventoryRecord
from app.validation.foundry_agent_drift_validator import FoundryAgentDriftValidator


def _agent(**overrides: object) -> AgentDefinition:
    defaults: dict[str, object] = {
        "id": "agent-a",
        "name": "Agent A",
        "role": "test_role",
        "description": "A test agent.",
        "foundry_agent_id": "agent-a-foundry",
        "owner": "test-team",
        "governance_policy_id": "standard-governance-policy-v1",
        "prompt_template_ref": "test-prompt",
        "model_deployment_ref": "test-model",
        "memory_access": ["shared"],
    }
    defaults.update(overrides)
    return AgentDefinition.model_validate(defaults)


def _record(**overrides: object) -> FoundryAgentInventoryRecord:
    defaults: dict[str, object] = {
        "agent_id": "agent-a",
        "display_name": "Agent A",
        "version": "1.0.0",
        "owner": "test-team",
        "foundry_agent_reference": "agent-a-foundry",
        "governance_policy_id": "standard-governance-policy-v1",
        "prompt_template_ref": "test-prompt",
        "model_deployment_ref": "test-model",
        "memory_scope": ["shared"],
        "lifecycle_state": "provisioned",
    }
    defaults.update(overrides)
    return FoundryAgentInventoryRecord.model_validate(defaults)


def test_validate_passes_for_config_with_no_workflow_agent_references(local_settings: Settings):
    result = FoundryAgentDriftValidator().validate(local_settings)

    assert result.passed


def test_validate_passes_for_real_repo_config(foundry_configured_settings: Settings):
    # foundry_configured_settings points at a hermetic tmp_path config tree
    # with no workflows referencing agents at all, so there is nothing to flag.
    result = FoundryAgentDriftValidator().validate(foundry_configured_settings)

    assert result.passed, result.issues


def test_validate_flags_workflow_step_referencing_disabled_agent(
    foundry_configured_settings: Settings, tmp_path: Path
):
    agents_dir = foundry_configured_settings.config_root / "agents"
    (agents_dir / "registry.yaml").write_text(
        "agents:\n"
        "  - id: test-agent\n"
        "    name: Test Agent\n"
        "    role: test_role\n"
        "    description: A minimal valid agent used for testing.\n"
        "    enabled: false\n",
        encoding="utf-8",
    )

    result = FoundryAgentDriftValidator().validate(foundry_configured_settings)

    assert not result.passed
    assert any("Lifecycle drift" in issue.message for issue in result.issues)


def test_detect_drift_returns_empty_report_when_no_previous_record():
    report = FoundryAgentDriftValidator().detect_drift(_agent(), None)

    assert report.issues == []


def test_detect_drift_flags_version_mismatch():
    report = FoundryAgentDriftValidator().detect_drift(
        _agent(version="2.0.0"), _record(version="1.0.0")
    )

    assert any(issue.issue_type == "version_mismatch" for issue in report.issues)


def test_detect_drift_flags_deployment_mismatch():
    report = FoundryAgentDriftValidator().detect_drift(
        _agent(model_deployment_ref="gpt-4o"), _record(model_deployment_ref="test-model")
    )

    assert any(issue.issue_type == "deployment_mismatch" for issue in report.issues)


def test_detect_drift_flags_governance_policy_mismatch_as_critical():
    report = FoundryAgentDriftValidator().detect_drift(
        _agent(governance_policy_id="new-policy"),
        _record(governance_policy_id="standard-governance-policy-v1"),
    )

    issue = next(i for i in report.issues if i.issue_type == "governance_policy_mismatch")
    assert issue.severity == "critical"
    assert report.has_critical_issues


def test_detect_drift_flags_prompt_mismatch():
    report = FoundryAgentDriftValidator().detect_drift(
        _agent(prompt_template_ref="new-prompt"), _record(prompt_template_ref="test-prompt")
    )

    assert any(issue.issue_type == "prompt_mismatch" for issue in report.issues)


def test_detect_drift_flags_ownership_mismatch():
    report = FoundryAgentDriftValidator().detect_drift(
        _agent(owner="new-team"), _record(owner="test-team")
    )

    assert any(issue.issue_type == "ownership_mismatch" for issue in report.issues)


def test_detect_drift_flags_memory_configuration_mismatch():
    report = FoundryAgentDriftValidator().detect_drift(
        _agent(memory_access=["shared", "enterprise"]), _record(memory_scope=["shared"])
    )

    assert any(issue.issue_type == "memory_configuration_mismatch" for issue in report.issues)


def test_detect_drift_flags_lifecycle_mismatch_when_enabled_but_previously_deprecated():
    report = FoundryAgentDriftValidator().detect_drift(
        _agent(enabled=True), _record(lifecycle_state="deprecated")
    )

    issue = next(i for i in report.issues if i.issue_type == "lifecycle_mismatch")
    assert issue.severity == "critical"


def test_detect_drift_returns_no_issues_when_nothing_changed():
    report = FoundryAgentDriftValidator().detect_drift(_agent(), _record())

    assert report.issues == []
