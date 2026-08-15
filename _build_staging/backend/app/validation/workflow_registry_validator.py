"""Validates the workflow registry configuration can be loaded successfully.

Full workflow execution (the orchestration engine) is implemented in
Phase 6; this validator enforces the Validation Requirements in
``.github/copilot-instructions.md`` by ensuring the workflow registry
under ``Settings.workflows_path`` exists, parses, validates, and only
references agents that exist in the agent registry.
"""
from __future__ import annotations

from app.agents.registry import AgentRegistry, AgentRegistryError
from app.config.settings import Settings
from app.validation.base import ValidationResult
from app.workflows.registry import WorkflowRegistry, WorkflowRegistryError


class WorkflowRegistryValidator:
    """Fails closed if the workflow registry is missing, invalid, or references
    unknown agents.
    """

    name = "WorkflowRegistryValidator"

    def validate(self, settings: Settings) -> ValidationResult:
        try:
            workflow_registry = WorkflowRegistry.load(settings.workflows_path)
        except WorkflowRegistryError as exc:
            return ValidationResult.fail(self.name, [str(exc)])

        try:
            agent_registry = AgentRegistry.load(settings.agents_path, default_llm=settings.default_llm)
        except AgentRegistryError:
            # AgentRegistryValidator is responsible for reporting agent
            # registry problems; skip the cross-reference check here rather
            # than duplicating that failure.
            return ValidationResult.ok(self.name)

        errors = workflow_registry.validate_agent_references(agent_registry)
        if errors:
            return ValidationResult.fail(self.name, errors)
        return ValidationResult.ok(self.name)
