"""Deterministic requirement coverage checks for existing mission stages."""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Final

from app.deploy_launch.models import RequirementFidelityItem, RequirementFidelityReport

__all__ = [
    "create_fidelity_report",
    "extract_requirement_ids",
    "missing_requirement_ids",
    "record_fidelity_execution",
    "record_test_coverage",
]

_REQUIREMENT_ID_PATTERN: Final = re.compile(r"\bREQ-\d{3,}\b", re.IGNORECASE)
_REQUIREMENT_ID_DIGITS_PATTERN: Final = re.compile(r"^REQ-(\d+)$", re.IGNORECASE)
_TEST_FUNCTION_PATTERN: Final = re.compile(
    r"\b(?:async\s+)?def\s+(test_[A-Za-z0-9_]+)\s*\(", re.IGNORECASE
)


def extract_requirement_ids(text: str) -> tuple[str, ...]:
    """Returns unique requirement ids in source order."""

    return tuple(
        dict.fromkeys(match.group(0).upper() for match in _REQUIREMENT_ID_PATTERN.finditer(text))
    )


def missing_requirement_ids(
    requirements_text: str, evidence_texts: str | Iterable[str]
) -> tuple[str, ...]:
    """Returns approved requirement ids absent from the supplied stage evidence."""

    if isinstance(evidence_texts, str):
        combined_evidence = evidence_texts
    else:
        combined_evidence = "\n".join(evidence_texts)
    covered_ids = set(extract_requirement_ids(combined_evidence))
    return tuple(
        requirement_id
        for requirement_id in extract_requirement_ids(requirements_text)
        if requirement_id not in covered_ids
    )


def _requirement_statements(text: str) -> tuple[tuple[str, str], ...]:
    statements: dict[str, str] = {}
    for line in text.splitlines():
        requirement_ids = extract_requirement_ids(line)
        for requirement_id in requirement_ids:
            if requirement_id in statements:
                continue
            statement = _REQUIREMENT_ID_PATTERN.sub("", line, count=1)
            statement = re.sub(r"^[\s\-\*\[\]\d.)]+", "", statement).strip()
            statements[requirement_id] = statement or requirement_id
    return tuple(statements.items())


def create_fidelity_report(
    requirements_text: str, *, max_repair_attempts: int
) -> RequirementFidelityReport:
    requirements = [
        RequirementFidelityItem(requirement_id=requirement_id, statement=statement)
        for requirement_id, statement in _requirement_statements(requirements_text)
    ]
    return RequirementFidelityReport(
        status="pending" if requirements else "failed",
        requirements=requirements,
        total_requirements=len(requirements),
        max_repair_attempts=max_repair_attempts,
        gaps=[] if requirements else ["No approved REQ IDs are available for validation."],
    )


def _requirement_name_pattern(requirement_id: str) -> re.Pattern[str]:
    """Builds a name-matching pattern that tolerates a generated test function
    dropping a requirement id's leading zero(s) - e.g. writing
    ``test_req_16_...`` for ``REQ-016`` - while still requiring a real digit
    boundary on both sides so it can never match an unrelated id (a plain
    substring check would wrongly match ``REQ-016`` against a test written
    for ``REQ-0160``, and would wrongly miss ``REQ-016`` against a test that
    dropped its leading zero to ``test_req_16_...``)."""
    match = _REQUIREMENT_ID_DIGITS_PATTERN.match(requirement_id)
    digits = match.group(1) if match else requirement_id.rsplit("-", 1)[-1]
    significant = digits.lstrip("0") or "0"
    return re.compile(rf"(?<!\d)req_0*{re.escape(significant)}(?!\d)", re.IGNORECASE)


def _tagged_test_names(module: str) -> dict[str, list[str]]:
    """Maps each requirement id declared in a ``# REQ-xxx`` comment directly
    above a test function to that function's name.

    This is the primary, authoritative coverage signal - it only requires
    the agent to copy an id verbatim into a comment (the exact string it was
    already given in the approved requirements), never to correctly
    transform it into a valid, zero-padded Python identifier. Encoding the id
    into the function name (checked separately by ``_requirement_name_pattern``
    as a fallback) has repeatedly proven fragile: pytest reports a
    parametrized test's real name with a bracketed suffix, and an agent can
    drop a requirement id's leading zero when forming an identifier - both
    are real bugs this repo hit. A pending tag survives blank lines and
    decorator lines (e.g. ``@pytest.mark.parametrize(...)``) between the
    comment and the ``def`` it labels, but is cleared by any other line so a
    tag can never leak onto an unrelated, later function.
    """

    tags_by_id: dict[str, list[str]] = {}
    pending_ids: list[str] = []
    for line in module.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            for match in _REQUIREMENT_ID_PATTERN.finditer(stripped):
                requirement_id = match.group(0).upper()
                if requirement_id not in pending_ids:
                    pending_ids.append(requirement_id)
            continue
        if stripped.startswith("@"):
            continue
        def_match = _TEST_FUNCTION_PATTERN.search(line)
        if def_match and pending_ids:
            test_name = def_match.group(1)
            for requirement_id in pending_ids:
                tags_by_id.setdefault(requirement_id, []).append(test_name)
        pending_ids = []
    return tags_by_id


def _test_names_for_requirement(requirement_id: str, modules: Iterable[str]) -> list[str]:
    pattern = _requirement_name_pattern(requirement_id)
    names: list[str] = []
    for module in modules:
        names.extend(_tagged_test_names(module).get(requirement_id, ()))
        module_names = _TEST_FUNCTION_PATTERN.findall(module)
        names.extend(name for name in module_names if pattern.search(name))
    return list(dict.fromkeys(names))


def record_test_coverage(
    report: RequirementFidelityReport, modules: Iterable[str]
) -> RequirementFidelityReport:
    module_list = list(modules)
    requirements: list[RequirementFidelityItem] = []
    for item in report.requirements:
        test_names = _test_names_for_requirement(item.requirement_id, module_list)
        requirements.append(
            item.model_copy(
                update={
                    "status": "covered" if test_names else "missing",
                    "test_names": test_names,
                    "evidence": (
                        "Executable acceptance test generated."
                        if test_names
                        else "No executable acceptance test name contains this requirement ID."
                    ),
                }
            )
        )
    covered = sum(item.status == "covered" for item in requirements)
    total = report.total_requirements
    gaps = [
        f"{item.requirement_id}: no executable acceptance test"
        for item in requirements
        if item.status == "missing"
    ]
    return report.model_copy(
        update={
            "status": "testing" if total > 0 and covered == total else "failed",
            "requirements": requirements,
            "covered_requirements": covered,
            "coverage_percent": 0.0 if total == 0 else round(covered * 100 / total, 1),
            "gaps": (
                gaps
                if total > 0
                else ["No approved REQ IDs are available for validation."]
            ),
        }
    )


def _observed_names_for(expected_name: str, observed_names: set[str]) -> set[str]:
    """Returns every observed JUnit test-case name that corresponds to
    ``expected_name`` - either an exact match, or (for a
    ``@pytest.mark.parametrize``-decorated test) any of its bracketed case
    instances, e.g. ``test_foo[korean]``. pytest's JUnit XML always reports
    a parametrized test's real case name as ``"<def name>[<param id>]"``,
    never the bare ``def`` name alone - matching by exact equality would
    wrongly report every passing parametrized test as having no JUnit
    result at all, even though it actually ran and passed."""

    prefix = expected_name + "["
    return {
        name for name in observed_names if name == expected_name or name.startswith(prefix)
    }


def record_fidelity_execution(
    report: RequirementFidelityReport,
    *,
    success: bool,
    summary: str,
    passed_test_names: Iterable[str] = (),
    failed_test_names: Iterable[str] = (),
    errored_test_names: Iterable[str] = (),
    skipped_test_names: Iterable[str] = (),
    final_failure: bool = False,
    execution_incomplete: bool = False,
) -> RequirementFidelityReport:
    passed_names = set(passed_test_names)
    failed_names = set(failed_test_names)
    errored_names = set(errored_test_names)
    skipped_names = set(skipped_test_names)
    all_observed_names = passed_names | failed_names | errored_names | skipped_names
    requirements: list[RequirementFidelityItem] = []
    gaps: list[str] = []
    for item in report.requirements:
        expected_names = set(item.test_names)
        failed: list[str] = []
        errored: list[str] = []
        skipped: list[str] = []
        unobserved: list[str] = []
        item_passed = bool(expected_names)
        for expected_name in sorted(expected_names):
            instances = _observed_names_for(expected_name, all_observed_names)
            if not instances:
                unobserved.append(expected_name)
                item_passed = False
                continue
            if not instances <= passed_names:
                item_passed = False
            failed.extend(sorted(instances & failed_names))
            errored.extend(sorted(instances & errored_names))
            skipped.extend(sorted(instances & skipped_names))
        if item_passed:
            evidence = "Passed: " + ", ".join(sorted(expected_names))
            status = "passed"
        elif execution_incomplete and not all_observed_names:
            # The pytest subprocess itself never finished (e.g. it was killed
            # for exceeding the execution timeout) - nothing at all was
            # observed for ANY requirement, not just this one. Reporting
            # "no JUnit result: <name>" per requirement here would read as a
            # test-name-matching bug (the class of bug this file has already
            # fixed three times - parametrize brackets, dropped leading
            # zeros, fragile name matching), when the real, actionable cause
            # is that the whole suite never ran to completion.
            evidence = f"Test execution did not finish: {summary}"
            status = "failed"
            gaps.append(f"{item.requirement_id}: {evidence}")
        else:
            details = []
            if failed:
                details.append("failed: " + ", ".join(failed))
            if errored:
                details.append("errored: " + ", ".join(errored))
            if skipped:
                details.append("skipped: " + ", ".join(skipped))
            if unobserved:
                details.append("no JUnit result: " + ", ".join(unobserved))
            if not expected_names:
                details.append("no named acceptance test")
            evidence = "; ".join(details)
            status = "failed"
            gaps.append(f"{item.requirement_id}: {evidence}")
        requirements.append(item.model_copy(update={"status": status, "evidence": evidence}))

    passed = sum(item.status == "passed" for item in requirements)
    all_requirements_passed = (
        success and report.total_requirements > 0 and passed == report.total_requirements
    )
    return report.model_copy(
        update={
            "status": (
                "passed"
                if all_requirements_passed
                else ("failed" if final_failure else "repairing")
            ),
            "requirements": requirements,
            "passed_requirements": passed,
            "pass_percent": (
                0.0
                if report.total_requirements == 0
                else round(passed * 100 / report.total_requirements, 1)
            ),
            "gaps": gaps,
            "execution_summary": summary,
        }
    )