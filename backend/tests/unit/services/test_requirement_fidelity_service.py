from app.services.requirement_fidelity_service import (
    create_fidelity_report,
    extract_requirement_ids,
    missing_requirement_ids,
    record_fidelity_execution,
    record_test_coverage,
)


def test_extract_requirement_ids_deduplicates_and_preserves_source_order() -> None:
    text = "[REQ-002] Second. [req-001] First. REQ-002 repeated."

    assert extract_requirement_ids(text) == ("REQ-002", "REQ-001")


def test_missing_requirement_ids_reports_partial_coverage() -> None:
    requirements = "[REQ-001] Upload. [REQ-002] Analyze. [REQ-003] Export."

    assert missing_requirement_ids(requirements, ["# covers REQ-001", "covers REQ-003"]) == (
        "REQ-002",
    )


def test_legacy_requirements_without_ids_do_not_create_false_failures() -> None:
    assert missing_requirement_ids("The mission must search documents.", "search is covered") == ()


def test_fidelity_report_lists_each_requirement_and_candid_partial_coverage() -> None:
    report = create_fidelity_report(
        """
Must-Have Functional Requirements:
- [REQ-001] Process every uploaded document.
- [REQ-002] Export a signed result.
Critical path:
- [REQ-001] Process every uploaded document.
""",
        max_repair_attempts=3,
    )

    report = record_test_coverage(
        report,
        [
            """
# REQ-001
def test_req_001_processes_every_document():
    assert process_all()
"""
        ],
    )

    assert report.total_requirements == 2
    assert report.covered_requirements == 1
    assert report.coverage_percent == 50.0
    assert report.status == "failed"
    assert report.requirements[0].test_names == ["test_req_001_processes_every_document"]
    assert report.requirements[1].status == "missing"
    assert report.gaps == ["REQ-002: no executable acceptance test"]


def test_fidelity_execution_only_reaches_one_hundred_percent_after_real_pass() -> None:
    report = create_fidelity_report(
        "[REQ-001] Process every document.", max_repair_attempts=3
    )
    report = record_test_coverage(
        report,
        ["# REQ-001\ndef test_req_001():\n    assert process_all()"],
    )

    repairing = record_fidelity_execution(
        report, success=False, summary="1 failed", final_failure=False
    )
    passed = record_fidelity_execution(
        report, success=True, summary="1 passed", final_failure=False
    )

    assert repairing.status == "repairing"
    assert repairing.pass_percent == 0
    assert repairing.requirements[0].evidence == "1 failed"
    assert passed.status == "passed"
    assert passed.pass_percent == 100
    assert passed.requirements[0].status == "passed"