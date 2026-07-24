"""Workflow registry: loads workflow definitions from externalized configuration.

Full workflow execution (the orchestration engine) is implemented in
Phase 6; this module only loads and validates *what* workflows exist and
that they reference known steps and agents.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from app.agents.registry import AgentRegistry
from app.utils.yaml_loader import YamlLoadError, iter_yaml_files, load_yaml_file
from app.workflows.models import WorkflowDefinition


class WorkflowRegistryError(RuntimeError):
    """Raised when the workflow registry configuration is missing or invalid."""


class WorkflowRegistry:
    """An immutable, in-memory registry of workflow definitions."""

    def __init__(self, definitions: dict[str, WorkflowDefinition]) -> None:
        self._definitions = definitions

    @classmethod
    def load(cls, directory: Path) -> WorkflowRegistry:
        """Load and validate every workflow definition under ``directory``.

        Every YAML file must define a top-level ``workflows`` list. Raises
        ``WorkflowRegistryError`` if the directory has no YAML files, any
        file is malformed, any definition fails schema validation, any two
        definitions share the same ``id``, or any step depends on an
        unknown step within the same workflow.
        """

        files = iter_yaml_files(directory)
        if not files:
            raise WorkflowRegistryError(f"No workflow registry files found under '{directory}'.")

        definitions: dict[str, WorkflowDefinition] = {}
        for file_path in files:
            try:
                document = load_yaml_file(file_path)
            except YamlLoadError as exc:
                raise WorkflowRegistryError(str(exc)) from exc

            if not isinstance(document, dict) or "workflows" not in document:
                raise WorkflowRegistryError(
                    f"'{file_path}' must define a top-level 'workflows' list."
                )

            raw_workflows = document["workflows"]
            if not isinstance(raw_workflows, list):
                raise WorkflowRegistryError(f"'workflows' in '{file_path}' must be a list.")

            for raw_workflow in raw_workflows:
                try:
                    workflow = WorkflowDefinition.model_validate(raw_workflow)
                except ValidationError as exc:
                    raise WorkflowRegistryError(
                        f"Invalid workflow definition in '{file_path}': {exc}"
                    ) from exc

                if workflow.id in definitions:
                    raise WorkflowRegistryError(
                        f"Duplicate workflow id '{workflow.id}' found in '{file_path}'."
                    )

                step_ids = {step.id for step in workflow.steps}
                for step in workflow.steps:
                    for dependency in step.depends_on:
                        if dependency not in step_ids:
                            raise WorkflowRegistryError(
                                f"Workflow '{workflow.id}' step '{step.id}' depends on "
                                f"unknown step '{dependency}'."
                            )

                definitions[workflow.id] = workflow

        if not definitions:
            raise WorkflowRegistryError(f"Workflow registry under '{directory}' is empty.")

        return cls(definitions)

    def get(self, workflow_id: str) -> WorkflowDefinition:
        try:
            return self._definitions[workflow_id]
        except KeyError as exc:
            raise KeyError(f"Unknown workflow id '{workflow_id}'.") from exc

    def list(self) -> list[WorkflowDefinition]:
        return list(self._definitions.values())

    def validate_agent_references(self, agent_registry: AgentRegistry) -> list[str]:
        """Return an error message for every step referencing an unknown agent."""

        errors: list[str] = []
        for workflow in self._definitions.values():
            for step in workflow.steps:
                if step.agent_id not in agent_registry:
                    errors.append(
                        f"Workflow '{workflow.id}' step '{step.id}' references "
                        f"unknown agent '{step.agent_id}'."
                    )
        return errors

    def __contains__(self, workflow_id: str) -> bool:
        return workflow_id in self._definitions

    def __len__(self) -> int:
        return len(self._definitions)
