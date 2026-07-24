"""Fail-closed tests for WorkflowRegistryValidator."""
from __future__ import annotations

import shutil

from app.validation.workflow_registry_validator import WorkflowRegistryValidator


def test_passes_when_registry_populated(local_settings):
    assert WorkflowRegistryValidator().validate(local_settings).passed


def test_fails_closed_when_directory_missing(local_settings):
    shutil.rmtree(local_settings.workflows_path)
    result = WorkflowRegistryValidator().validate(local_settings)
    assert not result.passed


def test_fails_closed_when_directory_empty(local_settings):
    for item in local_settings.workflows_path.iterdir():
        item.unlink()
    result = WorkflowRegistryValidator().validate(local_settings)
    assert not result.passed


def test_fails_closed_when_workflow_references_unknown_agent(local_settings):
    bad_workflow = local_settings.workflows_path / "bad.yaml"
    bad_workflow.write_text(
        "workflows:\n"
        "  - id: bad-workflow\n"
        "    name: Bad Workflow\n"
        "    description: References an agent that does not exist.\n"
        "    steps:\n"
        "      - id: step-one\n"
        "        agent_id: does-not-exist\n"
        "        description: A step.\n",
        encoding="utf-8",
    )
    result = WorkflowRegistryValidator().validate(local_settings)
    assert not result.passed
    assert any("does-not-exist" in issue.message for issue in result.issues)
