from app.services.requirement_fidelity_service import (
    extract_requirement_ids,
    missing_requirement_ids,
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
