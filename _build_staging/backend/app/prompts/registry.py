"""Prompt template registry: loads prompt templates from externalized configuration.

Full prompt resolution and execution is implemented alongside
AzureAgentGateway in Phase 3; this module only loads and validates *what*
prompt templates exist and that every ``{placeholder}`` used in a
template's text is explicitly declared in its ``variables`` list.
"""
from __future__ import annotations

import string
from pathlib import Path

from pydantic import ValidationError

from app.prompts.models import PromptTemplate
from app.utils.yaml_loader import YamlLoadError, iter_yaml_files, load_yaml_file


class PromptRegistryError(RuntimeError):
    """Raised when the prompt template registry configuration is missing or invalid."""


def _template_placeholders(template: str) -> set[str]:
    """Return the set of named ``{placeholder}`` fields used in ``template``."""

    formatter = string.Formatter()
    return {field_name for _, field_name, _, _ in formatter.parse(template) if field_name}


class PromptRegistry:
    """An immutable, in-memory registry of prompt templates."""

    def __init__(self, definitions: dict[str, PromptTemplate]) -> None:
        self._definitions = definitions

    @classmethod
    def load(cls, directory: Path) -> PromptRegistry:
        """Load and validate every prompt template under ``directory``.

        Every YAML file must define a top-level ``prompts`` list. Raises
        ``PromptRegistryError`` if the directory has no YAML files, any
        file is malformed, any definition fails schema validation, any
        template uses an undeclared variable, or any two definitions share
        the same ``id``.
        """

        files = iter_yaml_files(directory)
        if not files:
            raise PromptRegistryError(f"No prompt template files found under '{directory}'.")

        definitions: dict[str, PromptTemplate] = {}
        for file_path in files:
            try:
                document = load_yaml_file(file_path)
            except YamlLoadError as exc:
                raise PromptRegistryError(str(exc)) from exc

            if not isinstance(document, dict) or "prompts" not in document:
                raise PromptRegistryError(
                    f"'{file_path}' must define a top-level 'prompts' list."
                )

            raw_prompts = document["prompts"]
            if not isinstance(raw_prompts, list):
                raise PromptRegistryError(f"'prompts' in '{file_path}' must be a list.")

            for raw_prompt in raw_prompts:
                try:
                    prompt = PromptTemplate.model_validate(raw_prompt)
                except ValidationError as exc:
                    raise PromptRegistryError(
                        f"Invalid prompt template in '{file_path}': {exc}"
                    ) from exc

                try:
                    placeholders = _template_placeholders(prompt.template)
                except ValueError as exc:
                    raise PromptRegistryError(
                        f"Prompt '{prompt.id}' in '{file_path}' has a malformed "
                        f"template: {exc}"
                    ) from exc

                undeclared = placeholders - set(prompt.variables)
                if undeclared:
                    raise PromptRegistryError(
                        f"Prompt '{prompt.id}' in '{file_path}' uses undeclared "
                        f"variable(s): {sorted(undeclared)}."
                    )

                if prompt.id in definitions:
                    raise PromptRegistryError(
                        f"Duplicate prompt id '{prompt.id}' found in '{file_path}'."
                    )
                definitions[prompt.id] = prompt

        if not definitions:
            raise PromptRegistryError(f"Prompt template registry under '{directory}' is empty.")

        return cls(definitions)

    def get(self, prompt_id: str) -> PromptTemplate:
        try:
            return self._definitions[prompt_id]
        except KeyError as exc:
            raise KeyError(f"Unknown prompt id '{prompt_id}'.") from exc

    def list(self) -> list[PromptTemplate]:
        return list(self._definitions.values())

    def __contains__(self, prompt_id: str) -> bool:
        return prompt_id in self._definitions

    def __len__(self) -> int:
        return len(self._definitions)
