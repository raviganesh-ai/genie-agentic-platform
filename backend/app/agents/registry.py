"""Agent registry: loads agent definitions from externalized configuration.

Full agent execution (the Azure AI Foundry gateway) is implemented in
Phase 3; this module only loads and validates *what* agents exist.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from app.agents.models import AgentDefinition
from app.config.settings import DEFAULT_LLM
from app.utils.yaml_loader import YamlLoadError, iter_yaml_files, load_yaml_file


class AgentRegistryError(RuntimeError):
    """Raised when the agent registry configuration is missing or invalid."""


class AgentRegistry:
    """An immutable, in-memory registry of agent definitions."""

    def __init__(self, definitions: dict[str, AgentDefinition]) -> None:
        self._definitions = definitions

    @classmethod
    def load(cls, directory: Path, default_llm: str = DEFAULT_LLM) -> AgentRegistry:
        """Load and validate every agent definition under ``directory``.

        Every YAML file must define a top-level ``agents`` list. Raises
        ``AgentRegistryError`` if the directory has no YAML files, any file
        is malformed, any definition fails schema validation, or any two
        definitions share the same ``id``.

        Any agent that omits ``model_deployment_ref`` falls back to
        ``default_llm`` (normally ``Settings.default_llm``), so every agent
        in the returned registry always has a concrete, non-blank LLM.
        """
        if not default_llm.strip():
            raise AgentRegistryError("default_llm must not be blank.")

        files = iter_yaml_files(directory)
        if not files:
            raise AgentRegistryError(f"No agent registry files found under '{directory}'.")

        definitions: dict[str, AgentDefinition] = {}
        for file_path in files:
            try:
                document = load_yaml_file(file_path)
            except YamlLoadError as exc:
                raise AgentRegistryError(str(exc)) from exc

            if not isinstance(document, dict) or "agents" not in document:
                raise AgentRegistryError(f"'{file_path}' must define a top-level 'agents' list.")

            raw_agents = document["agents"]
            if not isinstance(raw_agents, list):
                raise AgentRegistryError(f"'agents' in '{file_path}' must be a list.")

            for raw_agent in raw_agents:
                try:
                    agent = AgentDefinition.model_validate(raw_agent)
                except ValidationError as exc:
                    raise AgentRegistryError(
                        f"Invalid agent definition in '{file_path}': {exc}"
                    ) from exc

                if agent.model_deployment_ref is None:
                    agent = agent.model_copy(update={"model_deployment_ref": default_llm})

                if agent.id in definitions:
                    raise AgentRegistryError(
                        f"Duplicate agent id '{agent.id}' found in '{file_path}'."
                    )
                definitions[agent.id] = agent

        if not definitions:
            raise AgentRegistryError(f"Agent registry under '{directory}' is empty.")

        return cls(definitions)

    def get(self, agent_id: str) -> AgentDefinition:
        try:
            return self._definitions[agent_id]
        except KeyError as exc:
            raise KeyError(f"Unknown agent id '{agent_id}'.") from exc

    def list(self) -> list[AgentDefinition]:
        return list(self._definitions.values())

    def __contains__(self, agent_id: str) -> bool:
        return agent_id in self._definitions

    def __len__(self) -> int:
        return len(self._definitions)
