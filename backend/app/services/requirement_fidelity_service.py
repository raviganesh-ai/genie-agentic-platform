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


def _test_names_for_requirement(requirement_id: str, modules: Iterable[str]) -> list[str]:
    normalized_id = requirement_id.lower().replace("-", "_")
    names: list[str] = []
    for module in modules:
        module_names = _TEST_FUNCTION_PATTERN.findall(module)
        names.extend(name for name in module_names if normalized_id in name.lower())
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
) -> RequirementFidelityReport:
    passed_names = set(passed_test_names)
    failed_names = set(failed_test_names)
    errored_names = set(errored_test_names)
    skipped_names = set(skipped_test_names)
    requirements: list[RequirementFidelityItem] = []
    gaps: list[str] = []
    for item in report.requirements:
        expected_names = set(item.test_names)
        failed = sorted(expected_names & failed_names)
        errored = sorted(expected_names & errored_names)
        skipped = sorted(expected_names & skipped_names)
        unobserved = sorted(
            expected_names - passed_names - failed_names - errored_names - skipped_names
        )
        item_passed = bool(expected_names) and expected_names <= passed_names
        if item_passed:
            evidence = "Passed: " + ", ".join(sorted(expected_names))
            status = "passed"
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