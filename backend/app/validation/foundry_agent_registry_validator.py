"""Foundry agent registry validator (Phase 10A).

Fail-closed, configuration-only (no network) startup check that every
enabled agent that will execute through ``AzureAgentGateway`` in
production carries the metadata Foundry provisioning/synchronization
requires: a Foundry agent reference, version, governance policy id,
resolvable prompt template reference, deployment reference, ownership,
and non-empty memory scope. Mirrors ``ProductionSafetyValidator``'s
"no-op outside production" shape exactly - this is an *additional*,
separately invoked validator (see ``app.main``'s lifespan), never inserted
into the fixed ``StartupValidationRunner.default_validators()`` list
protected by the Validation Requirements in
``.github/copilot-instructions.md``.
"""
from __future__ import annotations

from app.agents.registry import AgentRegistry, AgentRegistryError
from app.config.settings import Settings
from app.prompts.registry import PromptRegistry, PromptRegistryError
from app.validation.base import ValidationResult

__all__ = ["FoundryAgentRegistryValidator"]


class FoundryAgentRegistryValidator:
    """Fails closed in production if any enabled agent's Foundry metadata is incomplete."""

    name = "FoundryAgentRegistryValidator"

    def validate(self, settings: Settings) -> ValidationResult:
        if settings.provider_mode != "production":
            return ValidationResult.ok(self.name)

        try:
            agent_registry = AgentRegistry.load(settings.agents_path, default_llm=settings.default_llm)
            prompt_registry = PromptRegistry.load(settings.prompts_path)
        except (AgentRegistryError, PromptRegistryError) as exc:
            return ValidationResult.fail(self.name, [str(exc)])

        errors: list[str] = []
        for agent in agent_registry.list():
            if not agent.enabled:
                continue

            if not agent.foundry_agent_id:
                errors.append(f"Agent '{agent.id}': foundryAgentReference is missing.")
            if not agent.version.strip():
                errors.append(f"Agent '{agent.id}': agent version is missing.")
            if not agent.governance_policy_id:
                errors.append(f"Agent '{agent.id}': governancePolicyId is missing.")
            if not agent.prompt_template_ref:
                errors.append(f"Agent '{agent.id}': promptTemplateRef is missing.")
            elif agent.prompt_template_ref not in prompt_registry:
                errors.append(
                    f"Agent '{agent.id}': promptTemplateRef "
                    f"'{agent.prompt_template_ref}' does not exist in the prompt registry."
                )
            if not agent.model_deployment_ref:
                errors.append(f"Agent '{agent.id}': deployment reference is missing.")
            if not agent.owner:
                errors.append(f"Agent '{agent.id}': owner metadata is missing.")
            if not agent.memory_access:
                errors.append(f"Agent '{agent.id}': memory scope is empty.")

        if errors:
            return ValidationResult.fail(self.name, errors)
        return ValidationResult.ok(self.name)
