"""Loads Azure resource provider requirements from externalized configuration."""
from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from app.deployment.models import ResourceProviderRequirement
from app.utils.yaml_loader import YamlLoadError, iter_yaml_files, load_yaml_file


class ResourceProviderRequirementError(RuntimeError):
    """Raised when the resource provider requirement configuration is missing or invalid."""


def load_resource_provider_requirements(directory: Path) -> list[ResourceProviderRequirement]:
    """Load and validate every resource provider requirement under ``directory``.

    Every YAML file must define a top-level ``resource_providers`` list.
    Raises ``ResourceProviderRequirementError`` if the directory has no YAML
    files, any file is malformed, any entry fails schema validation, or any
    two entries share the same ``namespace``.
    """

    files = iter_yaml_files(directory)
    if not files:
        raise ResourceProviderRequirementError(
            f"No resource provider requirement files found under '{directory}'."
        )

    requirements: dict[str, ResourceProviderRequirement] = {}
    for file_path in files:
        try:
            document = load_yaml_file(file_path)
        except YamlLoadError as exc:
            raise ResourceProviderRequirementError(str(exc)) from exc

        if not isinstance(document, dict) or "resource_providers" not in document:
            raise ResourceProviderRequirementError(
                f"'{file_path}' must define a top-level 'resource_providers' list."
            )

        raw_requirements = document["resource_providers"]
        if not isinstance(raw_requirements, list):
            raise ResourceProviderRequirementError(
                f"'resource_providers' in '{file_path}' must be a list."
            )

        for raw_requirement in raw_requirements:
            try:
                requirement = ResourceProviderRequirement.model_validate(raw_requirement)
            except ValidationError as exc:
                raise ResourceProviderRequirementError(
                    f"Invalid resource provider requirement in '{file_path}': {exc}"
                ) from exc

            if requirement.namespace in requirements:
                raise ResourceProviderRequirementError(
                    f"Duplicate resource provider namespace '{requirement.namespace}' "
                    f"found in '{file_path}'."
                )
            requirements[requirement.namespace] = requirement

    if not requirements:
        raise ResourceProviderRequirementError(
            f"Resource provider requirement registry under '{directory}' is empty."
        )

    return list(requirements.values())
