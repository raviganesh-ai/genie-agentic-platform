"""Deterministic requirement coverage checks for existing mission stages.

Only the shared, still-active requirement-id helpers live here now.
``create_fidelity_report``/``record_test_coverage``/``record_fidelity_execution``
(the Deploy & Launch "Requirement Fidelity Gate" that used to generate and
execute a pytest acceptance suite against every approved requirement) were
removed - Deploy & Launch now runs a non-blocking Microsoft Security
Copilot scan and an Azure FinOps cost report instead (see
``app.deploy_launch.security_copilot_gateway`` and
``app.deploy_launch.finops_cost_service``). ``extract_requirement_ids`` and
``missing_requirement_ids`` are kept as-is: they back the unrelated,
still-active deterministic requirement-to-component assignment used by
``app.agents.tools.architecture_parsing``,
``app.agents.tools.orchestration_tools``, and
``app.orchestration.workflow_step_executor``.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Final

__all__ = [
    "extract_requirement_ids",
    "missing_requirement_ids",
]

_REQUIREMENT_ID_PATTERN: Final = re.compile(r"\bREQ-\d{3,}\b", re.IGNORECASE)


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