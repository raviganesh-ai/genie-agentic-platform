"""Strongly typed prompt template registry domain models.

Instances are populated exclusively from YAML files under the configured
prompts directory (see ``Settings.prompts_path``); prompt text must never
be hardcoded in source, per the Configuration Rules in
``.github/copilot-instructions.md``.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PromptTemplate(BaseModel):
    """A single prompt template and its declared substitution variables."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    template: str = Field(min_length=1)
    variables: list[str] = Field(default_factory=list)
    version: str = "1.0.0"
