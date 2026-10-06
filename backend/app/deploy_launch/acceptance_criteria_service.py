"""Deterministic acceptance-criteria verification for a deployed mission.

Phase 7 of the Genie-SaS Build Alignment platform-change roadmap (ACI
Intelligent Developer Experience PoC Requirements, Section 22.2 item 7):
an acceptance-criteria verification stage that accepts a structured AC
list (e.g. Section 15's AC-001..AC-013) and reports pass/fail per
criterion against a live deployed mission's own real test run.

Deliberately reuses the SAME proven tagging convention
``app.services.requirement_fidelity_service`` already established for
"# REQ-XXX" and the "Goals:" section heading, rather than inventing a new
pipeline: an acceptance-criteria list is just another recognized section
heading ("Acceptance Criteria:") within the SAME approved requirements
text ``test-generation-v1`` already receives, and each criterion's own
dedicated test is tagged with a "# AC-XXX" comment exactly like a
requirement is tagged with "# REQ-XXX" - no new prompt variable or
workflow wiring needed to supply this. Verdicts are read from an
ALREADY-EXECUTED ``TestExecutionResult`` (see
``app.deploy_launch.test_execution_service.TestExecutionService``), never
fabricated or assumed - this module only maps real, observed pytest
outcomes back to the criterion that named them.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

__all__ = [
    "AcceptanceCriterion",
    "AcceptanceCriterionResult",
    "build_acceptance_criteria_report",
    "extract_acceptance_criteria",
]

_ACCEPTANCE_CRITERION_ID_PATTERN: Final = re.compile(r"\bAC-\d{3,}\b", re.IGNORECASE)
_SECTION_HEADING_PATTERN: Final = re.compile(r"^[^\s].*:\s*$")
_TEST_FUNCTION_PATTERN: Final = re.compile(
    r"\b(?:async\s+)?def\s+(test_[A-Za-z0-9_]+)\s*\(", re.IGNORECASE
)


@dataclass(frozen=True)
class AcceptanceCriterion:
    """One acceptance criterion declared under the requirements text's own
    "Acceptance Criteria:" heading (e.g. ``AC-001: A new user signs in...``)."""

    criterion_id: str
    statement: str


@dataclass(frozen=True)
class AcceptanceCriterionResult:
    """The real, observed verdict for one ``AcceptanceCriterion``, derived
    from an already-executed ``TestExecutionResult``.

    ``status`` is one of:
    - ``"passed"``: every test tagged with this criterion's id passed.
    - ``"failed"``: at least one tagged test ran and failed.
    - ``"errored"``: at least one tagged test errored (e.g. setup/collection).
    - ``"not_covered"``: no generated test was tagged with this criterion's
      id at all, or a tagged test never produced an observed JUnit result
      (e.g. execution timed out before it could run).
    """

    criterion_id: str
    statement: str
    status: str
    test_names: tuple[str, ...] = ()


def extract_acceptance_criteria(requirements_text: str) -> tuple[AcceptanceCriterion, ...]:
    """Returns every criterion declared under a recognized "Acceptance
    Criteria:" heading within the approved requirements text, in source
    order - the same section-heading convention already used for "Goals:"
    (see ``requirement_fidelity_service._goal_requirement_ids``), so no
    new prompt variable or workflow wiring is needed to supply this.
    Returns an empty tuple when the requirements text declares no such
    section - acceptance-criteria verification is opt-in per mission."""

    criteria: dict[str, str] = {}
    in_section = False
    for line in requirements_text.splitlines():
        stripped = line.strip()
        if stripped.casefold() == "acceptance criteria:":
            in_section = True
            continue
        if in_section and _SECTION_HEADING_PATTERN.fullmatch(stripped):
            break
        if not in_section:
            continue
        match = _ACCEPTANCE_CRITERION_ID_PATTERN.search(stripped)
        if match is None:
            continue
        criterion_id = match.group(0).upper()
        if criterion_id in criteria:
            continue
        statement = _ACCEPTANCE_CRITERION_ID_PATTERN.sub("", stripped, count=1)
        statement = re.sub(r"^[\s\-*\[\]:.)]+", "", statement).strip()
        criteria[criterion_id] = statement or criterion_id
    return tuple(
        AcceptanceCriterion(criterion_id=criterion_id, statement=statement)
        for criterion_id, statement in criteria.items()
    )


def _tagged_test_names(modules: Iterable[str]) -> dict[str, list[str]]:
    """Maps each criterion id declared in a ``# AC-XXX`` comment directly
    above a test function to that function's name - mirroring
    ``requirement_fidelity_service._tagged_test_names`` exactly, including
    tolerating blank lines/decorator lines between the comment and the
    ``def`` it labels."""

    tags_by_id: dict[str, list[str]] = {}
    for module in modules:
        pending_ids: list[str] = []
        for line in module.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                for match in _ACCEPTANCE_CRITERION_ID_PATTERN.finditer(stripped):
                    criterion_id = match.group(0).upper()
                    if criterion_id not in pending_ids:
                        pending_ids.append(criterion_id)
                continue
            if stripped.startswith("@"):
                continue
            def_match = _TEST_FUNCTION_PATTERN.search(line)
            if def_match and pending_ids:
                test_name = def_match.group(1)
                for criterion_id in pending_ids:
                    tags_by_id.setdefault(criterion_id, []).append(test_name)
            pending_ids = []
    return tags_by_id


def _observed_names_for(expected_name: str, observed_names: set[str]) -> set[str]:
    """Mirrors ``requirement_fidelity_service._observed_names_for``: a
    parametrized test's real JUnit case name always carries a bracketed
    suffix (e.g. ``test_foo[korean]``), never the bare ``def`` name alone."""

    prefix = expected_name + "["
    return {name for name in observed_names if name == expected_name or name.startswith(prefix)}


def build_acceptance_criteria_report(
    criteria: Iterable[AcceptanceCriterion],
    modules: Iterable[str],
    *,
    passed_test_names: Iterable[str] = (),
    failed_test_names: Iterable[str] = (),
    errored_test_names: Iterable[str] = (),
) -> tuple[AcceptanceCriterionResult, ...]:
    """Maps each acceptance criterion to the real, observed pytest outcome
    of its own dedicated "# AC-XXX"-tagged test(s) from an ALREADY-RUN
    ``TestExecutionResult`` - never a fabricated or assumed verdict. A
    criterion with more than one tagged test "passed"es only when every one
    of them passed; any single error/failure among them marks the whole
    criterion accordingly."""

    tags_by_id = _tagged_test_names(modules)
    passed_names = set(passed_test_names)
    failed_names = set(failed_test_names)
    errored_names = set(errored_test_names)
    all_observed = passed_names | failed_names | errored_names

    results: list[AcceptanceCriterionResult] = []
    for criterion in criteria:
        expected_names = tags_by_id.get(criterion.criterion_id, [])
        if not expected_names:
            results.append(
                AcceptanceCriterionResult(
                    criterion_id=criterion.criterion_id,
                    statement=criterion.statement,
                    status="not_covered",
                )
            )
            continue

        statuses: set[str] = set()
        for expected_name in expected_names:
            instances = _observed_names_for(expected_name, all_observed)
            if not instances:
                statuses.add("not_covered")
            elif instances & errored_names:
                statuses.add("errored")
            elif instances & failed_names:
                statuses.add("failed")
            elif instances <= passed_names:
                statuses.add("passed")

        if "errored" in statuses:
            status = "errored"
        elif "failed" in statuses:
            status = "failed"
        elif "not_covered" in statuses:
            status = "not_covered"
        else:
            status = "passed"

        results.append(
            AcceptanceCriterionResult(
                criterion_id=criterion.criterion_id,
                statement=criterion.statement,
                status=status,
                test_names=tuple(expected_names),
            )
        )
    return tuple(results)
