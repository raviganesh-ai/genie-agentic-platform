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
    allowed_tool_names: list[str] | None = Field(
        default=None,
        description=(
            "Optional restriction of which of this step's agent's own "
            "AgentDefinition.tool_definitions may be exposed for this step's "
            "execution (see AgentExecutionRequest.allowed_tool_names). Used "
            "by genie-orchestrator-driven phase steps so each phase only "
            "exposes the delegation tool(s) relevant to that phase."
        ),
    )
    variable_sources: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Declares where each of this step's prompt variables comes from when "
            "the caller does not explicitly supply it in a run's step_inputs: "
            "the literal value 'transcript' resolves to the session's combined "
            "uploaded call transcript/recording text; the value 'step:<step_id>' "
            "resolves to that already-completed step's output_text. Any variable "
            "not listed here (e.g. 'policies', 'failure_details') must still be "
            "supplied explicitly - this never auto-invents governance/policy "
            "content from a transcript."
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
