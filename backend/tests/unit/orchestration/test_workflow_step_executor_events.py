"""Unit tests for `WorkflowStepExecutor`'s live event agent-id attribution.

Every `solution-discovery-workflow` step's own configured `agent_id` is
`"genie-orchestrator"` (see `config/workflows/registry.yaml` - the
orchestrator delegates the real work via its one allowed
`call_<specialist>` tool). Live `step_started`/`step_delta`/
`step_completed`/`step_failed` events published to `WorkflowEventBus` (and
shown to users e.g. via `LiveWorkflowPulse`) must attribute to that real
specialist - never the orchestrator itself - matching the delegated tool
call's own `step_delta` events (see
`orchestration_tools._stream_and_publish_deltas`)."""
from __future__ import annotations

from app.orchestration.workflow_step_executor import _display_agent_id
from app.workflows.models import WorkflowStep


def _step(allowed_tool_names: list[str] | None) -> WorkflowStep:
    return WorkflowStep(
        id="design-architecture",
        agent_id="genie-orchestrator",
        description="Design the architecture.",
        allowed_tool_names=allowed_tool_names,
    )


def test_display_agent_id_resolves_the_real_specialist_from_the_delegation_tool() -> None:
    step = _step(["call_architecture_designer"])

    assert _display_agent_id(step, "genie-orchestrator") == "architecture-designer"


def test_display_agent_id_falls_back_when_no_tool_names_are_configured() -> None:
    step = _step(None)

    assert _display_agent_id(step, "genie-orchestrator") == "genie-orchestrator"


def test_display_agent_id_falls_back_when_no_tool_name_resolves_to_a_delegation() -> None:
    step = _step(["not_a_delegation_tool"])

    assert _display_agent_id(step, "genie-orchestrator") == "genie-orchestrator"
