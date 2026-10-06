"""Unit tests for acceptance_criteria_service (Phase 7)."""
from __future__ import annotations

from app.deploy_launch.acceptance_criteria_service import (
    AcceptanceCriterion,
    build_acceptance_criteria_report,
    extract_acceptance_criteria,
)

_REQUIREMENTS_WITH_ACCEPTANCE_CRITERIA = """
Must-Have Functional Requirements:
- [REQ-001] A new user signs in through Microsoft Entra ID.

Acceptance Criteria:
- AC-001: A new user signs in through Microsoft Entra ID with the correct identity and role context.
- AC-002: The user enters a natural-language goal and receives a relevant capability bundle.

Non-Goals:
- Production Okta integration.
"""


def test_extract_acceptance_criteria_reads_the_recognized_section_heading():
    criteria = extract_acceptance_criteria(_REQUIREMENTS_WITH_ACCEPTANCE_CRITERIA)

    assert [c.criterion_id for c in criteria] == ["AC-001", "AC-002"]
    assert "signs in through Microsoft Entra ID" in criteria[0].statement
    assert "capability bundle" in criteria[1].statement


def test_extract_acceptance_criteria_returns_empty_when_section_absent():
    assert extract_acceptance_criteria("Must-Have Functional Requirements:\n- [REQ-001] Upload.") == ()


def test_extract_acceptance_criteria_stops_at_the_next_section_heading():
    criteria = extract_acceptance_criteria(_REQUIREMENTS_WITH_ACCEPTANCE_CRITERIA)

    # "Non-Goals:" bullet never leaks into the Acceptance Criteria section.
    assert all("Okta" not in c.statement for c in criteria)


def test_build_acceptance_criteria_report_reports_passed_failed_and_not_covered():
    criteria = (
        AcceptanceCriterion(criterion_id="AC-001", statement="Sign-in works."),
        AcceptanceCriterion(criterion_id="AC-002", statement="Discovery works."),
        AcceptanceCriterion(criterion_id="AC-003", statement="Never generated."),
    )
    modules = [
        """
# AC-001
def test_ac_001_sign_in():
    assert True

# AC-002
def test_ac_002_discovery():
    assert False
""",
    ]

    report = build_acceptance_criteria_report(
        criteria,
        modules,
        passed_test_names=["test_ac_001_sign_in"],
        failed_test_names=["test_ac_002_discovery"],
    )

    by_id = {result.criterion_id: result for result in report}
    assert by_id["AC-001"].status == "passed"
    assert by_id["AC-002"].status == "failed"
    assert by_id["AC-003"].status == "not_covered"
    assert by_id["AC-003"].test_names == ()


def test_build_acceptance_criteria_report_treats_errored_as_errored_not_failed():
    criteria = (AcceptanceCriterion(criterion_id="AC-001", statement="Sign-in works."),)
    modules = ["# AC-001\ndef test_ac_001_sign_in():\n    assert True\n"]

    report = build_acceptance_criteria_report(
        criteria, modules, errored_test_names=["test_ac_001_sign_in"]
    )

    assert report[0].status == "errored"


def test_build_acceptance_criteria_report_handles_parametrized_test_case_names():
    """A parametrized test's real JUnit case name always carries a
    bracketed suffix - never the bare def name alone."""

    criteria = (AcceptanceCriterion(criterion_id="AC-001", statement="Covers every region."),)
    modules = ["# AC-001\ndef test_ac_001_region():\n    assert True\n"]

    report = build_acceptance_criteria_report(
        criteria, modules, passed_test_names=["test_ac_001_region[us]", "test_ac_001_region[eu]"]
    )

    assert report[0].status == "passed"


def test_build_acceptance_criteria_report_not_covered_when_tagged_test_never_ran():
    """A tagged test that never produced any observed JUnit result (e.g.
    execution timed out) must never be silently reported as passed."""

    criteria = (AcceptanceCriterion(criterion_id="AC-001", statement="Sign-in works."),)
    modules = ["# AC-001\ndef test_ac_001_sign_in():\n    assert True\n"]

    report = build_acceptance_criteria_report(criteria, modules)

    assert report[0].status == "not_covered"
