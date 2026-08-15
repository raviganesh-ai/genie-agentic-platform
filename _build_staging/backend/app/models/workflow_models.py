"""Workflow execution runtime models: step inputs/results, run results, deliverables.

Distinct from ``app.workflows.models`` (the externally configured, static
``WorkflowDefinition``/``WorkflowStep`` registry entries): these models
represent the *output* of actually running a workflow via
``app.orchestration.workflow_runtime.WorkflowRuntime``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.workflow_state import WorkflowStatus

WorkflowStepStatus = Literal["completed", "failed"]

DeliverableType = Literal[
    "requirements_package",
    "architecture_package",
    "roadmap_package",
    "executive_summary_package",
    "final_output_package",
]

__all__ = [
    "DeliverablePackage",
    "DeliverableType",
    "WorkflowRunResult",
    "WorkflowStepInput",
    "WorkflowStepResult",
    "WorkflowStepStatus",
]


class WorkflowStepInput(BaseModel):
    """Caller-supplied input for a single workflow step execution."""

    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(min_length=1)
    variables: dict[str, str] = Field(default_factory=dict)
    prompt_id: str | None = Field(
        default=None,
        description="Overrides the step's registered prompt_id, if any, for this run.",
    )


class WorkflowStepResult(BaseModel):
    """The outcome of executing a single workflow step."""

    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    status: WorkflowStepStatus
    output_text: str | None = None
    error: str | None = None
    started_at: datetime
    completed_at: datetime
    resolved_variables: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "The prompt variables actually resolved for this execution. Carried "
            "forward as a base layer if this step is later re-executed on a "
            "resumed run (e.g. a customer chat message), so explicit overrides "
            "that cannot be re-derived from variable_sources - such as "
            "governance-review's 'policies' - are not lost on re-run."
        ),
    )


class WorkflowRunResult(BaseModel):
    """The full outcome of one workflow run, including every executed wave."""

    model_config = ConfigDict(extra="forbid")

    workflow_run_id: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    status: WorkflowStatus
    waves: list[list[str]] = Field(default_factory=list)
    step_results: list[WorkflowStepResult] = Field(default_factory=list)
    detail: str = ""
    agent_scope_id: str | None = Field(
        default=None,
        description=(
            "Dedicated-agent-fleet scope key for this run, if any (e.g. a "
            "requirement group id) - see SessionAgentResolver.resolve's "
            "scope_id parameter. None means this run uses the session-level "
            "fleet (or the shared catalog pool). Carried forward automatically "
            "whenever this run is resumed, so follow-up chat/reanalyze "
            "interactions keep reaching the same dedicated fleet."
        ),
    )


class DeliverablePackage(BaseModel):
    """A customer-facing deliverable assembled from completed step outputs.

    The orchestrator only *coordinates* assembly of already agent-produced
    content into ``sections`` (keyed by step id) - it never generates or
    edits the content itself.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    deliverable_type: DeliverableType
    session_id: str = Field(min_length=1)
    workflow_run_id: str = Field(min_length=1)
    generated_at: datetime
    sections: dict[str, str] = Field(default_factory=dict)
