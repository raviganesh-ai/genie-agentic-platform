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
        requirements=requirements,
        total_requirements=len(requirements),
        max_repair_attempts=max_repair_attempts,
    )


def _test_names_for_requirement(requirement_id: str, modules: Iterable[str]) -> list[str]:
    normalized_id = requirement_id.lower().replace("-", "_")
    names: list[str] = []
    for module in modules:
        if requirement_id not in extract_requirement_ids(module):
            continue
        module_names = _TEST_FUNCTION_PATTERN.findall(module)
        matching_names = [name for name in module_names if normalized_id in name.lower()]
        names.extend(matching_names or module_names)
    return list(dict.fromkeys(names))


def record_test_coverage(
    report: RequirementFidelityReport, modules: Iterable[str]
) -> RequirementFidelityReport:
    module_list = list(modules)
    covered_ids = set(extract_requirement_ids("\n".join(module_list)))
    requirements = [
        item.model_copy(
            update={
                "status": "covered" if item.requirement_id in covered_ids else "missing",
                "test_names": _test_names_for_requirement(item.requirement_id, module_list),
                "evidence": (
                    "Executable acceptance test generated."
                    if item.requirement_id in covered_ids
                    else "No executable acceptance test references this requirement."
                ),
            }
        )
        for item in report.requirements
    ]
    covered = sum(item.status == "covered" for item in requirements)
    total = report.total_requirements
    gaps = [
        f"{item.requirement_id}: no executable acceptance test"
        for item in requirements
        if item.status == "missing"
    ]
    return report.model_copy(
        update={
            "status": "testing" if covered == total else "failed",
            "requirements": requirements,
            "covered_requirements": covered,
            "coverage_percent": 100.0 if total == 0 else round(covered * 100 / total, 1),
            "gaps": gaps,
        }
    )


def record_fidelity_execution(
    report: RequirementFidelityReport,
    *,
    success: bool,
    summary: str,
    final_failure: bool = False,
) -> RequirementFidelityReport:
    requirements = [
        item.model_copy(
            update={
                "status": "passed" if success else "failed",
                "evidence": summary,
            }
        )
        for item in report.requirements
    ]
    passed = report.total_requirements if success else 0
    gaps = [] if success else [
        f"{item.requirement_id}: deployed acceptance suite failed"
        for item in report.requirements
    ]
    return report.model_copy(
        update={
            "status": "passed" if success else ("failed" if final_failure else "repairing"),
            "requirements": requirements,
            "passed_requirements": passed,
            "pass_percent": 100.0 if success or report.total_requirements == 0 else 0.0,
            "gaps": gaps,
            "execution_summary": summary,
        }
    )