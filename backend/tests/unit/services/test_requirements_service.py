"""Unit tests for RequirementsService's pure verdict-parsing helper.

Covers ``_parse_verdict`` directly with crafted agent output text (no real
LLM available in tests). Service-level "pending"/"undetermined"/unknown-run
behavior (which needs a real ``AgentOrchestrator`` running a hermetic
workflow) is covered in
``tests/integration/test_requirements_qualification.py`` instead.
"""
from __future__ import annotations

from app.services.requirements_service import _parse_verdict


def test_parse_verdict_extracts_qualified_and_reason():
    text = (
        "Some extracted requirements text.\n\n"
        "AGENTIC_WORKFLOW_QUALIFICATION: QUALIFIED\n"
        "QUALIFICATION_REASON: Multiple specialist agents must collaborate "
        "on open-ended architecture decisions."
    )

    result = _parse_verdict(text)

    assert result == (
        True,
        "Multiple specialist agents must collaborate on open-ended architecture decisions.",
    )


def test_parse_verdict_extracts_not_qualified_and_reason():
    text = (
        "Some extracted requirements text.\n\n"
        "AGENTIC_WORKFLOW_QUALIFICATION: NOT_QUALIFIED\n"
        "QUALIFICATION_REASON: This is a single deterministic lookup with no "
        "ambiguity or planning required."
    )

    result = _parse_verdict(text)

    assert result == (
        False,
        "This is a single deterministic lookup with no ambiguity or planning required.",
    )


def test_parse_verdict_is_case_insensitive():
    text = "agentic_workflow_qualification: qualified\nqualification_reason: Reasoning required."

    result = _parse_verdict(text)

    assert result == (True, "Reasoning required.")


def test_parse_verdict_returns_none_when_no_verdict_present():
    text = "[local-agent-gateway] agent='requirements-analyst' resolved_prompt_length=42"

    assert _parse_verdict(text) is None


def test_parse_verdict_returns_none_reason_when_reason_line_missing():
    text = "AGENTIC_WORKFLOW_QUALIFICATION: QUALIFIED"

    assert _parse_verdict(text) == (True, None)
