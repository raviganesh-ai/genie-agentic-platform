"""Unit tests for RequirementsService's pure verdict-parsing helper.

Covers ``_parse_verdict`` directly with crafted agent output text (no real
LLM available in tests). Service-level "pending"/"undetermined"/unknown-run
behavior (which needs a real ``AgentOrchestrator`` running a hermetic
workflow) is covered in
``tests/integration/test_requirements_qualification.py`` instead.
"""
from __future__ import annotations

from app.services.requirements_service import _parse_open_decisions, _parse_verdict


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


# Genie platform-change Phase D: requirements-extraction-v1 now requires an
# "Open Decisions Requiring Customer Confirmation:" heading for any
# requirement defaulted to MUST-HAVE because it was ambiguous - these tests
# prove the parser actually recognizes the real contract, not a guess.
def test_parse_open_decisions_returns_empty_list_when_heading_is_absent():
    # The same "omit a heading with no items" convention every other
    # heading in this prompt already uses - absence means "none found",
    # not "could not verify".
    text = (
        "Goals:\n"
        "- [REQ-001] Users can submit a claim.\n\n"
        "Must-Have Functional Requirements:\n"
        "- [REQ-002] The system validates claim amounts.\n"
    )

    assert _parse_open_decisions(text) == []


def test_parse_open_decisions_extracts_items_under_the_heading():
    text = (
        "Must-Have Functional Requirements:\n"
        "- [REQ-003] The system supports multi-currency claims.\n\n"
        "Open Decisions Requiring Customer Confirmation:\n"
        "- [REQ-003] The transcript did not state which currencies are in "
        "scope; assumed USD and EUR only.\n"
        "- [REQ-007] \"Reasonably fast\" response time was not quantified; "
        "assumed under 2 seconds.\n\n"
        "Critical path (must build first):\n"
        "1. [REQ-003] The system supports multi-currency claims.\n"
    )

    result = _parse_open_decisions(text)

    assert result == [
        "[REQ-003] The transcript did not state which currencies are in "
        "scope; assumed USD and EUR only.",
        "[REQ-007] \"Reasonably fast\" response time was not quantified; "
        "assumed under 2 seconds.",
    ]


def test_parse_open_decisions_is_case_insensitive_on_the_heading():
    text = "open decisions requiring customer confirmation:\n- [REQ-009] Ambiguous scope.\n"

    assert _parse_open_decisions(text) == ["[REQ-009] Ambiguous scope."]


def test_parse_open_decisions_stops_at_the_next_heading_with_no_blank_line():
    text = (
        "Open Decisions Requiring Customer Confirmation:\n"
        "- [REQ-010] Ambiguous scope.\n"
        "Acceptance Criteria:\n"
        "- [AC-001] Something testable.\n"
    )

    assert _parse_open_decisions(text) == ["[REQ-010] Ambiguous scope."]
