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
        report,
        success=False,
        summary="1 failed",
        failed_test_names=["test_req_001"],
        final_failure=False,
    )
    passed = record_fidelity_execution(
        report,
        success=True,
        summary="1 passed",
        passed_test_names=["test_req_001"],
        final_failure=False,
    )

    assert repairing.status == "repairing"
    assert repairing.pass_percent == 0
    assert repairing.requirements[0].evidence == "failed: test_req_001"
    assert passed.status == "passed"
    assert passed.pass_percent == 100
    assert passed.requirements[0].status == "passed"


def test_comment_only_requirement_mention_is_not_executable_coverage() -> None:
    report = create_fidelity_report("[REQ-001] Process every document.", max_repair_attempts=3)

    report = record_test_coverage(
        report,
        [
            "def test_process_every_document():\n    # narrative mention of REQ-001 "
            + "inside the body does not count - only a comment directly above the def, "
            + "or the id in the def's own name, counts as coverage.\n    assert process_all()"
        ],
    )

    assert report.coverage_percent == 0
    assert report.requirements[0].status == "missing"


def test_zero_id_baseline_and_unobserved_test_results_fail_closed() -> None:
    empty_report = create_fidelity_report(
        "Process every document.", max_repair_attempts=3
    )
    empty_result = record_fidelity_execution(
        empty_report, success=True, summary="1 passed", passed_test_names=["test_something"]
    )
    report = create_fidelity_report("[REQ-001] Process every document.", max_repair_attempts=3)
    report = record_test_coverage(
        report,
        ["def test_req_001_process_every_document():\n    assert process_all()"],
    )
    unobserved = record_fidelity_execution(report, success=True, summary="1 passed")

    assert empty_result.status != "passed"
    assert empty_result.pass_percent == 0
    assert unobserved.status == "repairing"
    assert "no JUnit result" in unobserved.requirements[0].evidence


def test_parametrized_test_case_names_are_matched_to_their_bare_def_name() -> None:
    """pytest's JUnit XML reports a ``@pytest.mark.parametrize``-decorated
    test's real case names as ``"<def name>[<param id>]"`` - never the bare
    ``def`` name alone. Regression test for a real bug where every requirement
    covered by a parametrized test (a natural way to test "cover these N
    languages/items") always showed "no JUnit result found", even though
    every parametrized case actually ran and passed."""

    report = create_fidelity_report(
        "[REQ-025] Evaluate all seven target languages.", max_repair_attempts=3
    )
    report = record_test_coverage(
        report,
        [
            "@pytest.mark.parametrize('language', LANGUAGES)\ndef test_req_025_language_coverage(language):\n    assert covers(language)"
        ],
    )

    all_passed = record_fidelity_execution(
        report,
        success=True,
        summary="7 passed",
        passed_test_names=[
            "test_req_025_language_coverage[korean]",
            "test_req_025_language_coverage[german]",
            "test_req_025_language_coverage[spanish]",
            "test_req_025_language_coverage[italian]",
            "test_req_025_language_coverage[french]",
            "test_req_025_language_coverage[simplified_chinese]",
            "test_req_025_language_coverage[japanese]",
        ],
    )

    assert all_passed.status == "passed"
    assert all_passed.pass_percent == 100
    assert all_passed.requirements[0].status == "passed"

    one_failing = record_fidelity_execution(
        report,
        success=False,
        summary="6 passed, 1 failed",
        passed_test_names=[
            "test_req_025_language_coverage[korean]",
            "test_req_025_language_coverage[german]",
            "test_req_025_language_coverage[spanish]",
            "test_req_025_language_coverage[italian]",
            "test_req_025_language_coverage[french]",
            "test_req_025_language_coverage[simplified_chinese]",
        ],
        failed_test_names=["test_req_025_language_coverage[japanese]"],
        final_failure=False,
    )

    assert one_failing.status == "repairing"
    assert one_failing.requirements[0].status == "failed"
    assert "failed: test_req_025_language_coverage[japanese]" in one_failing.requirements[0].evidence
    assert "no JUnit result" not in one_failing.requirements[0].evidence


def test_test_name_matching_tolerates_a_dropped_leading_zero() -> None:
    """Regression test for a real bug where the Test Generation Agent wrote
    ``test_req_16_...`` (no leading zero) for a requirement whose approved id
    is ``REQ-016``, causing an exact/substring match against ``req_016`` to
    permanently report that requirement as having no executable test at all,
    even though a real, correctly-behaved test for it existed."""

    report = create_fidelity_report(
        "[REQ-016] Reassign an unresolved ticket to another agent.", max_repair_attempts=3
    )

    report = record_test_coverage(
        report,
        ["def test_req_16_reassigns_unresolved_ticket():\n    assert reassign()"],
    )

    assert report.requirements[0].status == "covered"
    assert report.requirements[0].test_names == ["test_req_16_reassigns_unresolved_ticket"]


def test_test_name_matching_does_not_collide_with_a_similar_numeric_id() -> None:
    """A dropped-zero-tolerant match must still respect digit boundaries -
    REQ-016's test must never be confused with REQ-160's, and vice versa."""

    report = create_fidelity_report(
        "[REQ-016] First requirement.\n[REQ-160] Second, unrelated requirement.",
        max_repair_attempts=3,
    )

    report = record_test_coverage(
        report,
        [
            "def test_req_016_first_behavior():\n    assert first()",
            "def test_req_160_second_behavior():\n    assert second()",
        ],
    )

    by_id = {item.requirement_id: item for item in report.requirements}
    assert by_id["REQ-016"].test_names == ["test_req_016_first_behavior"]
    assert by_id["REQ-160"].test_names == ["test_req_160_second_behavior"]


def test_tag_comment_covers_a_requirement_regardless_of_function_name() -> None:
    """The ``# REQ-xxx`` tag comment directly above a test is the primary,
    authoritative coverage signal - it must credit a requirement even when
    the test function's own name has nothing to do with that id at all,
    since asking an agent to correctly transform an id into a valid,
    zero-padded Python identifier has repeatedly proven fragile."""

    report = create_fidelity_report(
        "[REQ-050] Escalate a ticket after three failed retries.", max_repair_attempts=3
    )

    report = record_test_coverage(
        report,
        ["# REQ-050\ndef test_escalation_flow():\n    assert escalate()"],
    )

    assert report.requirements[0].status == "covered"
    assert report.requirements[0].test_names == ["test_escalation_flow"]


def test_tag_comment_survives_blank_lines_and_decorators_but_not_other_code() -> None:
    report = create_fidelity_report(
        "[REQ-060] First.\n[REQ-061] Second, untagged.", max_repair_attempts=3
    )

    report = record_test_coverage(
        report,
        [
            "# REQ-060\n\n@pytest.mark.parametrize('n', [1, 2])\ndef test_first(n):\n    assert "
            + "first(n)\n\nx = 1  # not a tag - this line must clear any pending tag\n# REQ-061\n"
            + "x = 2\ndef test_second():\n    assert second()"
        ],
    )

    by_id = {item.requirement_id: item for item in report.requirements}
    assert by_id["REQ-060"].test_names == ["test_first"]
    assert by_id["REQ-061"].status == "missing"


def test_one_tag_comment_block_covers_multiple_requirement_ids() -> None:
    report = create_fidelity_report(
        "[REQ-070] First.\n[REQ-071] Second.", max_repair_attempts=3
    )

    report = record_test_coverage(
        report,
        ["# REQ-070\n# REQ-071\ndef test_combined_behavior():\n    assert combined()"],
    )

    by_id = {item.requirement_id: item for item in report.requirements}
    assert by_id["REQ-070"].test_names == ["test_combined_behavior"]
    assert by_id["REQ-071"].test_names == ["test_combined_behavior"]