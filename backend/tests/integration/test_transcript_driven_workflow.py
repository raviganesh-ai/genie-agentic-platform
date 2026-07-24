"""Integration test: transcript/step-output auto-wiring via variable_sources.

Proves the Phase 1 ("add Azure Speech capabilities and using Call
transcripts") chain works end to end through the real
``WorkflowRuntime``/``WorkflowStepExecutor``: a run's ``transcript_text``
flows into any step whose ``variable_sources`` maps a variable to
``"transcript"``, and one step's output flows into a later step whose
``variable_sources`` maps a variable to ``"step:<id>"`` - all without the
caller supplying that variable explicitly via ``step_inputs``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.orchestration.agent_orchestrator import create_agent_orchestrator

from ._orchestration_helpers import build_orchestration_settings

_TRANSCRIPT_TEXT = "Customer call transcript: we need a self-service portal."


@pytest.fixture
def orchestrator(tmp_path: Path):
    settings = build_orchestration_settings(tmp_path / "config")
    return create_agent_orchestrator(settings=settings)


async def test_transcript_text_is_auto_wired_into_a_mapped_step(orchestrator) -> None:
    result = await orchestrator.run_workflow(
        workflow_id="transcript-workflow",
        session_id="session-transcript",
        trace_id="trace-1",
        transcript_text=_TRANSCRIPT_TEXT,
    )

    step_transcript_result = next(s for s in result.step_results if s.step_id == "step-transcript")
    expected_length = len(f"Process input {_TRANSCRIPT_TEXT}")
    assert f"resolved_prompt_length={expected_length}" in step_transcript_result.output_text


async def test_a_later_step_is_auto_wired_from_an_earlier_steps_output(orchestrator) -> None:
    result = await orchestrator.run_workflow(
        workflow_id="transcript-workflow",
        session_id="session-transcript-chained",
        trace_id="trace-1",
        transcript_text=_TRANSCRIPT_TEXT,
    )

    step_transcript_result = next(s for s in result.step_results if s.step_id == "step-transcript")
    step_chained_result = next(s for s in result.step_results if s.step_id == "step-chained")

    expected_length = len(f"Process input {step_transcript_result.output_text}")
    assert f"resolved_prompt_length={expected_length}" in step_chained_result.output_text


async def test_explicit_step_input_overrides_variable_sources(orchestrator) -> None:
    from app.models.workflow_models import WorkflowStepInput

    result = await orchestrator.run_workflow(
        workflow_id="transcript-workflow",
        session_id="session-transcript-override",
        trace_id="trace-1",
        transcript_text=_TRANSCRIPT_TEXT,
        step_inputs={
            "step-transcript": WorkflowStepInput(
                step_id="step-transcript", variables={"x": "explicit-override"}
            ),
        },
    )

    step_transcript_result = next(s for s in result.step_results if s.step_id == "step-transcript")
    expected_length = len("Process input explicit-override")
    assert f"resolved_prompt_length={expected_length}" in step_transcript_result.output_text
