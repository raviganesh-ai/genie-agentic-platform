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

__all__ = ["ArchitectureBuildPlan", "parse_architecture_build_plan"]

_SECTION_PATTERN = re.compile(
    r"##\s*Multi-Agent Workflow(.*?)(?=\n##\s|\Z)", re.DOTALL | re.IGNORECASE
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
