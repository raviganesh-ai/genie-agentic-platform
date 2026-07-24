"""Strongly typed workflow registry domain models.

Instances are populated exclusively from YAML files under the configured
workflows directory (see ``Settings.workflows_path``); workflow definitions
must never be hardcoded in source, per the Configuration Rules in
``.github/copilot-instructions.md``.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class WorkflowStep(BaseModel):
    """A single step within a workflow, executed by one agent.

    ``prompt_id``, ``requires_approval_checkpoint``, and
    ``required_memory_references`` are optional, additive fields consumed
    by the Phase 6 orchestration runtime (``app.orchestration``); a step
    with none of them set behaves exactly as it did in Phase 2/registry
    validation contexts.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    prompt_id: str | None = Field(
        default=None,
        description="Registered prompt template id this step resolves and sends to its agent.",
    )
    requires_approval_checkpoint: str | None = Field(
        default=None,
        description=(
            "Id of an approval_policy.yaml checkpoint that must have an "
            "approved decision before this step may execute."
        ),
    )
    required_memory_references: list[str] = Field(
        default_factory=list,
        description=(
            "Shared Collaboration Memory keys that must already exist "
            "before this step may execute."
        ),
    )


class WorkflowDefinition(BaseModel):
    """A single workflow's externally configured steps."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    steps: list[WorkflowStep] = Field(min_length=1)
    enabled: bool = True
