"""Unit tests for WorkflowRegistry loading and validation."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.registry import AgentRegistry
from app.workflows.registry import WorkflowRegistry, WorkflowRegistryError


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _agent_registry(tmp_path: Path, agent_id: str = "agent-a") -> AgentRegistry:
    directory = tmp_path / "agents"
    _write(
        directory / "registry.yaml",
        f"agents:\n  - id: {agent_id}\n    name: A\n    role: r\n    description: d\n"
        "    model_deployment_ref: m\n",
    )
    return AgentRegistry.load(directory)


def test_loads_valid_workflow_with_dependencies(tmp_path: Path):
    _write(
        tmp_path / "registry.yaml",
        """
workflows:
  - id: wf-1
    name: Workflow One
    description: A workflow.
    steps:
      - id: step-1
        agent_id: agent-a
        description: First step.
      - id: step-2
        agent_id: agent-b
        description: Second step.
        depends_on:
          - step-1
""",
    )

    registry = WorkflowRegistry.load(tmp_path)

    assert len(registry) == 1
    assert "wf-1" in registry
    assert [step.id for step in registry.get("wf-1").steps] == ["step-1", "step-2"]


def test_fails_closed_when_directory_missing(tmp_path: Path):
    with pytest.raises(WorkflowRegistryError, match="No workflow registry files found"):
        WorkflowRegistry.load(tmp_path / "missing")


def test_loads_variable_sources_for_transcript_and_step_chaining(tmp_path: Path):
    _write(
        tmp_path / "registry.yaml",
        """
workflows:
  - id: wf-1
    name: Workflow One
    description: A workflow.
    steps:
      - id: step-1
        agent_id: agent-a
        description: First step.
        variable_sources:
          context: transcript
      - id: step-2
        agent_id: agent-b
        description: Second step.
        depends_on:
          - step-1
        variable_sources:
          input: "step:step-1"
""",
    )

    registry = WorkflowRegistry.load(tmp_path)
    steps = {step.id: step for step in registry.get("wf-1").steps}

    assert steps["step-1"].variable_sources == {"context": "transcript"}
    assert steps["step-2"].variable_sources == {"input": "step:step-1"}


def test_variable_sources_defaults_to_empty(tmp_path: Path):
    _write(
        tmp_path / "registry.yaml",
        """
workflows:
  - id: wf-1
    name: Workflow One
    description: A workflow.
    steps:
      - id: step-1
        agent_id: agent-a
        description: First step.
""",
    )

    registry = WorkflowRegistry.load(tmp_path)
    assert registry.get("wf-1").steps[0].variable_sources == {}


def test_fails_closed_on_unknown_step_dependency(tmp_path: Path):
    _write(
        tmp_path / "registry.yaml",
        """
workflows:
  - id: wf-1
    name: Workflow One
    description: A workflow.
    steps:
      - id: step-1
        agent_id: agent-a
        description: First step.
        depends_on:
          - does-not-exist
""",
    )

    with pytest.raises(WorkflowRegistryError, match="unknown step"):
        WorkflowRegistry.load(tmp_path)


def test_fails_closed_on_duplicate_workflow_id(tmp_path: Path):
    _write(
        tmp_path / "a.yaml",
        "workflows:\n  - id: dup\n    name: A\n    description: d\n"
        "    steps:\n      - id: s\n        agent_id: agent-a\n        description: d\n",
    )
    _write(
        tmp_path / "b.yaml",
        "workflows:\n  - id: dup\n    name: B\n    description: d\n"
        "    steps:\n      - id: s\n        agent_id: agent-a\n        description: d\n",
    )

    with pytest.raises(WorkflowRegistryError, match="Duplicate workflow id"):
        WorkflowRegistry.load(tmp_path)


def test_fails_closed_when_registry_empty(tmp_path: Path):
    _write(tmp_path / "registry.yaml", "workflows: []\n")

    with pytest.raises(WorkflowRegistryError, match="is empty"):
        WorkflowRegistry.load(tmp_path)


def test_validate_agent_references_detects_unknown_agent(tmp_path: Path):
    _write(
        tmp_path / "workflows" / "registry.yaml",
        """
workflows:
  - id: wf-1
    name: Workflow One
    description: A workflow.
    steps:
      - id: step-1
        agent_id: unknown-agent
        description: First step.
""",
    )
    workflow_registry = WorkflowRegistry.load(tmp_path / "workflows")
    agent_registry = _agent_registry(tmp_path, agent_id="agent-a")

    errors = workflow_registry.validate_agent_references(agent_registry)

    assert len(errors) == 1
    assert "unknown-agent" in errors[0]


def test_validate_agent_references_passes_for_known_agents(tmp_path: Path):
    _write(
        tmp_path / "workflows" / "registry.yaml",
        """
workflows:
  - id: wf-1
    name: Workflow One
    description: A workflow.
    steps:
      - id: step-1
        agent_id: agent-a
        description: First step.
""",
    )
    workflow_registry = WorkflowRegistry.load(tmp_path / "workflows")
    agent_registry = _agent_registry(tmp_path, agent_id="agent-a")

    assert workflow_registry.validate_agent_references(agent_registry) == []


def test_validate_agent_references_detects_unknown_allowed_tool_name(tmp_path: Path):
    _write(
        tmp_path / "agents" / "registry.yaml",
        "agents:\n"
        "  - id: agent-a\n"
        "    name: A\n"
        "    role: r\n"
        "    description: d\n"
        "    model_deployment_ref: m\n"
        "    tool_definitions:\n"
        "      - name: call_known\n"
        "        description: A known tool.\n",
    )
    agent_registry = AgentRegistry.load(tmp_path / "agents")
    _write(
        tmp_path / "workflows" / "registry.yaml",
        """
workflows:
  - id: wf-1
    name: Workflow One
    description: A workflow.
    steps:
      - id: step-1
        agent_id: agent-a
        description: First step.
        allowed_tool_names:
          - call_unknown
""",
    )
    workflow_registry = WorkflowRegistry.load(tmp_path / "workflows")

    errors = workflow_registry.validate_agent_references(agent_registry)

    assert len(errors) == 1
    assert "call_unknown" in errors[0]


def test_validate_agent_references_passes_for_known_allowed_tool_name(tmp_path: Path):
    _write(
        tmp_path / "agents" / "registry.yaml",
        "agents:\n"
        "  - id: agent-a\n"
        "    name: A\n"
        "    role: r\n"
        "    description: d\n"
        "    model_deployment_ref: m\n"
        "    tool_definitions:\n"
        "      - name: call_known\n"
        "        description: A known tool.\n",
    )
    agent_registry = AgentRegistry.load(tmp_path / "agents")
    _write(
        tmp_path / "workflows" / "registry.yaml",
        """
workflows:
  - id: wf-1
    name: Workflow One
    description: A workflow.
    steps:
      - id: step-1
        agent_id: agent-a
        description: First step.
        allowed_tool_names:
          - call_known
""",
    )
    workflow_registry = WorkflowRegistry.load(tmp_path / "workflows")

    assert workflow_registry.validate_agent_references(agent_registry) == []
