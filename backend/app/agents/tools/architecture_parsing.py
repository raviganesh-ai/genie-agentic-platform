"""Deterministically parses the Build Agent's per-component generation plan
out of an approved architecture document's own "## Multi-Agent Workflow"
section (never LLM-invented, mirroring the same deterministic-extraction
pattern ``app.deploy_launch.mission_agent_provisioning_service`` uses for
per-agent instructions).

Lets ``call_build_agent`` (see ``app.agents.tools.orchestration_tools``)
invoke the Build Agent once per component - each specialist agent, then
the Orchestrator Agent, then the UI - instead of one single combined
generation, so the Workshop page can stream each component's own code as
soon as it completes (see ``build-generation-component-v1`` in
``config/prompts/registry.yaml``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.requirement_fidelity_service import extract_requirement_ids

__all__ = [
    "ArchitectureBuildPlan",
    "parse_architecture_build_plan",
    "parse_component_requirement_assignments",
]

_SECTION_PATTERN = re.compile(
    r"##\s*Multi-Agent Workflow(.*?)(?=\n##\s|\Z)", re.DOTALL | re.IGNORECASE
)
_UI_SECTION_PATTERN = re.compile(
    r"##\s*Single-Page UI Design(.*?)(?=\n##\s|\Z)", re.DOTALL | re.IGNORECASE
)
_BULLET_PATTERN = re.compile(r"^\s*(?:[-*]\s+)?\*\*(.+?)\*\*\s*:", re.MULTILINE)


@dataclass(frozen=True)
class ArchitectureBuildPlan:
    """The ordered components the Build Agent should generate, one at a time."""

    specialist_agent_names: tuple[str, ...]
    orchestrator_agent_name: str


def parse_architecture_build_plan(architecture_document: str) -> ArchitectureBuildPlan | None:
    """Returns the ordered build plan for ``architecture_document``.

    Returns ``None`` when the document's "## Multi-Agent Workflow" section
    does not follow the expected "**<Agent name>**: <responsibility>"
    bullet convention (see ``architecture-recommendation-v1``) closely
    enough to parse deterministically - e.g. no bullets found, or not
    exactly one bullet naming an "Orchestrator Agent". Callers must treat
    ``None`` as "fall back to a single combined build generation" rather
    than fail the whole build over a structural parsing gap: this parser
    is purely a streaming-UX optimization, never a correctness or
    governance boundary.
    """

    section_match = _SECTION_PATTERN.search(architecture_document)
    section_text = section_match.group(1) if section_match else architecture_document

    names = [match.group(1).strip() for match in _BULLET_PATTERN.finditer(section_text)]
    if not names:
        return None

    orchestrator_names = [name for name in names if "orchestrator" in name.lower()]
    if len(orchestrator_names) != 1:
        return None

    orchestrator_name = orchestrator_names[0]
    specialists = tuple(name for name in names if name != orchestrator_name)
    if not specialists:
        return None

    return ArchitectureBuildPlan(
        specialist_agent_names=specialists, orchestrator_agent_name=orchestrator_name
    )


def _bullet_spans(section_text: str) -> list[tuple[str, str]]:
    """Returns ``(name, own_text)`` for every "**Name**: ..." bullet in
    ``section_text``, where ``own_text`` is that bullet's own prose only -
    from just after its "**Name**:" label to the start of the next bullet
    (or the end of the section) - so a requirement ID mentioned in one
    bullet is never misattributed to a neighboring one.
    """

    matches = list(_BULLET_PATTERN.finditer(section_text))
    spans: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section_text)
        spans.append((match.group(1).strip(), section_text[start:end]))
    return spans


def parse_component_requirement_assignments(
    architecture_document: str,
) -> dict[str, tuple[str, ...]]:
    """Deterministically maps each build component's own lowercased label to
    the approved requirement IDs its own architecture bullet actually
    mentions - never LLM-invented or re-inferred by the Build Agent at
    generation time.

    ``architecture-recommendation-v1`` already requires every approved
    requirement ID to literally appear in the responsibility text of at
    least one specialist/orchestrator bullet or UI zone bullet, so this is
    a pure extraction (via ``extract_requirement_ids``, the same REQ-###
    pattern the Requirement Fidelity Gate uses) over each bullet's own
    span, not a new prompt contract.

    Each specialist/orchestrator agent name (lowercased, matching
    ``ArchitectureBuildPlan``'s labels) maps to the IDs mentioned in its
    own "## Multi-Agent Workflow" bullet. Every UI zone's IDs are unioned
    under the single key ``"ui"`` (matching ``build-generation-component-v1``'s
    ``component_kind == "ui"`` - the UI is always generated as one
    component regardless of how many input zones the architecture
    describes). A component with no bullet, or whose bullet mentions no
    IDs (e.g. the Orchestrator, which coordinates rather than implements
    a specific requirement), is simply absent from the returned mapping -
    callers must treat a missing key as "no requirements assigned", not
    as a parsing failure.
    """

    assignments: dict[str, tuple[str, ...]] = {}

    workflow_match = _SECTION_PATTERN.search(architecture_document)
    workflow_text = workflow_match.group(1) if workflow_match else ""
    for name, own_text in _bullet_spans(workflow_text):
        requirement_ids = extract_requirement_ids(own_text)
        if requirement_ids:
            assignments[name.strip().lower()] = requirement_ids

    ui_match = _UI_SECTION_PATTERN.search(architecture_document)
    ui_text = ui_match.group(1) if ui_match else ""
    ui_ids: list[str] = []
    for _, own_text in _bullet_spans(ui_text):
        for requirement_id in extract_requirement_ids(own_text):
            if requirement_id not in ui_ids:
                ui_ids.append(requirement_id)
    if ui_ids:
        assignments["ui"] = tuple(ui_ids)

    return assignments
