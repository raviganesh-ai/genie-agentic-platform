"""Unit tests for PromptRegistry loading and validation."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.prompts.registry import PromptRegistry, PromptRegistryError


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_loads_valid_prompt_template(tmp_path: Path):
    _write(
        tmp_path / "registry.yaml",
        """
prompts:
  - id: greet-v1
    name: Greeting
    description: Greets a user by name.
    template: 'Hello {name}, welcome to {product}.'
    variables:
      - name
      - product
""",
    )

    registry = PromptRegistry.load(tmp_path)

    assert len(registry) == 1
    prompt = registry.get("greet-v1")
    assert prompt.template == "Hello {name}, welcome to {product}."


def test_fails_closed_when_directory_missing(tmp_path: Path):
    with pytest.raises(PromptRegistryError, match="No prompt template files found"):
        PromptRegistry.load(tmp_path / "missing")


def test_fails_closed_on_undeclared_variable(tmp_path: Path):
    _write(
        tmp_path / "registry.yaml",
        """
prompts:
  - id: greet-v1
    name: Greeting
    description: Greets a user by name.
    template: 'Hello {name}'
    variables: []
""",
    )

    with pytest.raises(PromptRegistryError, match="undeclared"):
        PromptRegistry.load(tmp_path)


def test_fails_closed_on_duplicate_prompt_id(tmp_path: Path):
    _write(
        tmp_path / "a.yaml",
        "prompts:\n  - id: dup\n    name: A\n    description: d\n"
        "    template: 'hi'\n    variables: []\n",
    )
    _write(
        tmp_path / "b.yaml",
        "prompts:\n  - id: dup\n    name: B\n    description: d\n"
        "    template: 'hi'\n    variables: []\n",
    )

    with pytest.raises(PromptRegistryError, match="Duplicate prompt id"):
        PromptRegistry.load(tmp_path)


def test_fails_closed_when_registry_empty(tmp_path: Path):
    _write(tmp_path / "registry.yaml", "prompts: []\n")

    with pytest.raises(PromptRegistryError, match="is empty"):
        PromptRegistry.load(tmp_path)
