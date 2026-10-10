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

TYPED ARCHITECTURE SCHEMA (component_type discriminator) - in addition to
the always-required "## Multi-Agent Workflow" and "## Single-Page UI
Design" sections above, an architecture document MAY also declare these
optional, additional top-level sections when a mission genuinely needs a
backend component that is not an agent or a UI zone:

- "## Deterministic Services" -> ``component_type == "deterministic_service"``
- "## Data Models" -> ``component_type == "data_model"``
- "## API Contracts" -> ``component_type == "api_contract"``
- "## Gateway Policies" -> ``component_type == "gateway_policy"``
- "## Identity Configuration" -> ``component_type == "identity_config"``
- "## UI Pages" -> ``component_type == "page_view"`` - an ordered list of
  additional, independently navigable pages (a catalog, a dashboard, an
  audit/trace view, ...), each described with the same bullet convention
  as a "## Single-Page UI Design" zone. When present, this REPLACES "##
  Single-Page UI Design" for this mission (Build generates one component
  per declared page plus a deterministic, non-LLM-authored routing shell
  that wires them together - see ``code_materializer.generate_routing_shell``)
  rather than the usual single input-only page. Most missions still only
  need "## Single-Page UI Design" and never declare this section at all.

Each uses the exact same "**<Name>**: <description> (fulfills REQ-XXX...)"
bullet convention as the two baseline sections, so this module's existing
bullet-parsing helpers apply unchanged. ``architecture-recommendation-v1``
instructs the Architecture Designer agent to emit "## Data Models"/"##
Deterministic Services"/"## API Contracts"/"## UI Pages" whenever a
mission's own requirements call for them, and - as of this module's
``gateway_policy``/``identity_config`` reasoning guidance - "## Gateway
Policies"/"## Identity Configuration" too, always as a matched pair, only
when the requirements genuinely call for real end-user sign-in (never a
blanket rule, never a fixed keyword list - the Architecture Designer
reasons about this from the requirements text itself). ``call_build_agent``/
``build-generation-component-v1`` generate real code for every one of
these component types, and Deploy & Launch's own pipeline consumes
``identity_config``/``gateway_policy`` output to wire a real MSAL sign-in
UI and a real APIM ``validate-azure-ad-token`` policy (see
``app.deploy_launch.pipeline_service``'s ``sync-frontend-integration``
step and ``app.deploy_launch.prototype_api_gateway_service.publish_api``'s
``require_sign_in``). Landing the schema and its parser ahead of (and
decoupled from) the generation prompts that would populate it was a
deliberate ordering choice, so a mission could never cite a requirement
under a section whose component Build could not yet produce - which
would have silently orphaned that requirement.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.requirement_fidelity_service import extract_requirement_ids

__all__ = [
    "ArchitectureBuildPlan",
    "check_identity_requirement_coverage",
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

# Ordered (component_type, section header) pairs for the optional sections
# documented above. Order here is the order these components are appended
# to ``ArchitectureBuildPlan.other_components`` - stable and deterministic,
# never dependent on dict/set iteration order.
_OTHER_COMPONENT_SECTIONS: tuple[tuple[str, str], ...] = (
    ("data_model", "Data Models"),
    ("deterministic_service", "Deterministic Services"),
    ("api_contract", "API Contracts"),
    ("gateway_policy", "Gateway Policies"),
    ("identity_config", "Identity Configuration"),
    ("page_view", "UI Pages"),
)
_OTHER_COMPONENT_SECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (
        component_type,
        re.compile(rf"##\s*{re.escape(header)}(.*?)(?=\n##\s|\Z)", re.DOTALL | re.IGNORECASE),
    )
    for component_type, header in _OTHER_COMPONENT_SECTIONS
)


@dataclass(frozen=True)
class ArchitectureBuildPlan:
    """The ordered components the Build Agent should generate, one at a time."""

    specialist_agent_names: tuple[str, ...]
    orchestrator_agent_name: str
    # (component_type, component_name) pairs parsed from the optional
    # "## Deterministic Services" / "## Data Models" / "## API Contracts" /
    # "## Gateway Policies" / "## Identity Configuration" sections - see
    # this module's docstring. Empty for every architecture document that
    # only uses the two baseline sections, which remains the common case.
    other_components: tuple[tuple[str, str], ...] = ()


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

    other_components: list[tuple[str, str]] = []
    for component_type, pattern in _OTHER_COMPONENT_SECTION_PATTERNS:
        match = pattern.search(architecture_document)
        if match is None:
            continue
        for bullet_name in (
            m.group(1).strip() for m in _BULLET_PATTERN.finditer(match.group(1))
        ):
            other_components.append((component_type, bullet_name))

    return ArchitectureBuildPlan(
        specialist_agent_names=specialists,
        orchestrator_agent_name=orchestrator_name,
        other_components=tuple(other_components),
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
    pattern Requirement Validation uses) over each bullet's own
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

    Each optional "## Deterministic Services" / "## Data Models" /
    "## API Contracts" / "## Gateway Policies" / "## Identity
    Configuration" bullet (see ``ArchitectureBuildPlan.other_components``)
    maps by its own lowercased name, exactly like a specialist agent -
    these are independently named components, never unioned the way UI
    zones are.
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

    for _component_type, pattern in _OTHER_COMPONENT_SECTION_PATTERNS:
        section_match = pattern.search(architecture_document)
        if section_match is None:
            continue
        for name, own_text in _bullet_spans(section_match.group(1)):
            requirement_ids = extract_requirement_ids(own_text)
            if requirement_ids:
                assignments[name.strip().lower()] = requirement_ids

    return assignments


# A deliberately small, literal list - real identity-provider names/
# protocols actually named in a requirements document, never a broad
# guess at "anything security-related" (a requirement that is merely
# "secure" or mentions API keys/rate limiting does not call for this -
# see architecture-recommendation-v1's own "## Identity Configuration"/
# "## Gateway Policies" guidance, which this function backstops).
_IDENTITY_PROVIDER_KEYWORDS: tuple[str, ...] = (
    "entra id",
    "azure ad",
    "azure active directory",
    "microsoft entra",
    "oauth",
    "openid connect",
    "oidc",
    "single sign-on",
    "sso",
)


def check_identity_requirement_coverage(
    *, approved_requirements: str, architecture_document: str
) -> str | None:
    """Returns a human-readable gap description when the approved
    requirements name a real identity provider/protocol but the
    architecture document declares neither "## Identity Configuration"
    nor "## Gateway Policies" to satisfy it - or ``None`` when there is
    no such gap.

    A deterministic backstop for architecture-recommendation-v1's own
    prompt-level reasoning guidance for these two sections - confirmed
    by direct, repeated, empirical observation (identical approved
    requirements, same model, two separate "Validate your Vision" runs)
    that prompt guidance alone does not reliably produce these sections
    every time a mission's requirements genuinely call for real sign-in.
    A security-relevant gap like this one must fail closed rather than
    silently proceed to Build with an incomplete architecture - the
    real, observed consequence being a deployed prototype whose own
    generated pages reference a "Sign in" control that was never built.

    Never itself decides a mission needs identity from a keyword list
    alone - only flags the specific, observed failure mode: real
    identity-provider language already appears in the requirements, but
    the architecture has no matching component for it at all.
    """

    requirements_lower = approved_requirements.lower()
    if not any(keyword in requirements_lower for keyword in _IDENTITY_PROVIDER_KEYWORDS):
        return None

    architecture_lower = architecture_document.lower()
    has_identity_config = "## identity configuration" in architecture_lower
    has_gateway_policy = "## gateway policies" in architecture_lower
    if has_identity_config and has_gateway_policy:
        return None

    missing = [
        label
        for present, label in (
            (has_identity_config, '"## Identity Configuration"'),
            (has_gateway_policy, '"## Gateway Policies"'),
        )
        if not present
    ]
    return (
        "The approved requirements name a real identity provider or protocol "
        "(Entra ID/Azure AD/OAuth/OIDC/SSO), but this architecture does not "
        "declare " + " and ".join(missing) + " to enforce it. Re-run UI & Agent "
        "Design, or explicitly confirm this mission genuinely needs no real "
        "end-user sign-in, before proceeding to Build."
    )
