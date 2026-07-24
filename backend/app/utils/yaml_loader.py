"""Shared helpers for loading YAML-based externalized configuration.

Used by the agent, workflow, and prompt template registries to read their
respective ``config/`` directories, per the Configuration Rules in
``.github/copilot-instructions.md``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_YAML_SUFFIXES = {".yaml", ".yml"}


class YamlLoadError(RuntimeError):
    """Raised when a YAML configuration file cannot be read or parsed."""


def iter_yaml_files(directory: Path) -> list[Path]:
    """Return all YAML files directly or transitively under ``directory``.

    Returns an empty list (rather than raising) when the directory does not
    exist; callers are responsible for treating "no files" as a validation
    failure where appropriate.
    """

    if not directory.is_dir():
        return []
    return sorted(
        path for path in directory.rglob("*") if path.is_file() and path.suffix in _YAML_SUFFIXES
    )


def load_yaml_file(path: Path) -> Any:
    """Parse a single YAML file, raising ``YamlLoadError`` on any failure."""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise YamlLoadError(f"Unable to read configuration file '{path}': {exc}") from exc

    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise YamlLoadError(f"Invalid YAML in configuration file '{path}': {exc}") from exc
