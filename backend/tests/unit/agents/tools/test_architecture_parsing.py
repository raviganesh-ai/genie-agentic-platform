"""Unit tests for `parse_architecture_build_plan`."""
from __future__ import annotations

from app.agents.tools.architecture_parsing import (
    check_identity_requirement_coverage,
    parse_architecture_build_plan,
    parse_component_requirement_assignments,
)

_ARCHITECTURE_DOCUMENT = """
## UI Design

- **Kickoff Screen** --> **Support Triage Orchestrator Agent**: lets the
  user describe their issue.

## Multi-Agent Workflow

- **Ticket Classifier Agent**: classifies the incoming issue by category.
- **Resolution Drafter Agent**: drafts a resolution based on the category.
- **Support Triage Orchestrator Agent**: the single entry point, sequences
  the classifier then the drafter and returns the final result.
"""


def test_parses_specialists_and_orchestrator_in_order():
    plan = parse_architecture_build_plan(_ARCHITECTURE_DOCUMENT)

    assert plan is not None
    assert plan.specialist_agent_names == (
        "Ticket Classifier Agent",
        "Resolution Drafter Agent",
    )
    assert plan.orchestrator_agent_name == "Support Triage Orchestrator Agent"


def test_bullets_without_leading_dash_are_also_parsed():
    document = """
## Multi-Agent Workflow

**Billing Agent**: handles billing questions.
**Billing Orchestrator Agent**: the single entry point.
"""

    plan = parse_architecture_build_plan(document)

    assert plan is not None
    assert plan.specialist_agent_names == ("Billing Agent",)
    assert plan.orchestrator_agent_name == "Billing Orchestrator Agent"


def test_returns_none_when_section_is_missing():
    assert parse_architecture_build_plan("## UI Design\n\nSome unrelated text.") is None


def test_returns_none_when_no_bullet_is_the_orchestrator():
    document = """
## Multi-Agent Workflow

- **Billing Agent**: handles billing questions.
- **Refund Agent**: handles refunds.
"""

    assert parse_architecture_build_plan(document) is None


def test_returns_none_when_more_than_one_bullet_names_orchestrator():
    document = """
## Multi-Agent Workflow

- **Billing Orchestrator Agent**: entry point one.
- **Refund Orchestrator Agent**: entry point two.
"""

    assert parse_architecture_build_plan(document) is None


def test_returns_none_when_orchestrator_is_the_only_bullet():
    document = """
## Multi-Agent Workflow

- **Solo Orchestrator Agent**: does everything itself.
"""

    assert parse_architecture_build_plan(document) is None


def test_requirement_assignments_are_extracted_per_bullets_own_text():
    document = """
## Single-Page UI Design

- **Kickoff Screen**: lets the user pick a category (REQ-001) and upload
  an evidence file (REQ-003).

## Multi-Agent Workflow

- **Ticket Classifier Agent**: classifies the incoming issue by category
  (REQ-001, REQ-002).
- **Resolution Drafter Agent**: drafts a resolution (REQ-004).
- **Support Triage Orchestrator Agent**: the single entry point,
  sequences the classifier then the drafter.
"""

    assignments = parse_component_requirement_assignments(document)

    assert assignments["ticket classifier agent"] == ("REQ-001", "REQ-002")
    assert assignments["resolution drafter agent"] == ("REQ-004",)
    assert assignments["ui"] == ("REQ-001", "REQ-003")
    # The Orchestrator's own bullet mentions no requirement IDs - it
    # coordinates the specialists above rather than implementing a
    # specific requirement itself, so it is simply absent, not an error.
    assert "support triage orchestrator agent" not in assignments


def test_requirement_assignments_is_empty_when_no_ids_appear_anywhere():
    document = """
## Multi-Agent Workflow

- **Billing Agent**: handles billing questions with no cited requirement.
"""

    assert parse_component_requirement_assignments(document) == {}


def test_other_components_are_empty_when_no_optional_sections_present():
    plan = parse_architecture_build_plan(_ARCHITECTURE_DOCUMENT)

    assert plan is not None
    assert plan.other_components == ()


def test_parses_optional_component_type_sections_in_declared_order():
    document = """
## Multi-Agent Workflow

- **Ticket Classifier Agent**: classifies the incoming issue by category.
- **Support Triage Orchestrator Agent**: the single entry point.

## Single-Page UI Design

- **Kickoff Screen**: lets the user describe their issue.

## Deterministic Services

- **Entitlement Checker**: validates a request against the entitlement store.

## Data Models

- **SandboxTenant**: the logical multi-tenant sandbox record.
- **Entitlement**: a product/API grant for one tenant.

## API Contracts

- **Payments API**: the OpenAPI contract for payment operations.

## Gateway Policies

- **APIM JWT Policy**: validates bearer tokens at the gateway.

## Identity Configuration

- **Entra ID Adapter**: the identity-provider adapter configuration.
"""

    plan = parse_architecture_build_plan(document)

    assert plan is not None
    assert plan.other_components == (
        ("data_model", "SandboxTenant"),
        ("data_model", "Entitlement"),
        ("deterministic_service", "Entitlement Checker"),
        ("api_contract", "Payments API"),
        ("gateway_policy", "APIM JWT Policy"),
        ("identity_config", "Entra ID Adapter"),
    )


def test_api_contract_describing_genuine_runtime_behavior_also_becomes_a_deterministic_service():
    """Regression guard for the "mock backend never deployed" incident: a
    real deployed mission's own "## API Contracts" bullet described itself
    as "Fully executable backends..." yet the Architecture Designer never
    added a matching "## Deterministic Services" bullet (confirmed across
    two independent regenerations with the strengthened prompt guidance
    alone) - so Build never generated real code for it and the documented
    routes 404'd when actually called. Detecting this language
    deterministically and emitting a matching deterministic_service entry
    closes the gap without depending on prompt-following fidelity."""

    document = """
## Multi-Agent Workflow

- **Ticket Classifier Agent**: classifies the incoming issue by category.
- **Support Triage Orchestrator Agent**: the single entry point.

## API Contracts

- **ACI Mock Core Payments API**: Fully executable backends for Account-to-Account
  Payments, with an OpenAPI spec and at least 8 named operations.
- **Legacy Partner API**: a documentation-only reference contract for an
  external system this mission never calls.
"""

    plan = parse_architecture_build_plan(document)

    assert plan is not None
    assert plan.other_components == (
        ("api_contract", "ACI Mock Core Payments API"),
        ("deterministic_service", "ACI Mock Core Payments API"),
        ("api_contract", "Legacy Partner API"),
    )


def test_api_contract_is_not_duplicated_when_matching_service_already_declared():
    """When the Architecture Designer DID correctly add a matching "##
    Deterministic Services" bullet for an executable-sounding contract,
    the heuristic must not add a redundant second service entry."""

    document = """
## Multi-Agent Workflow

- **Ticket Classifier Agent**: classifies the incoming issue by category.
- **Support Triage Orchestrator Agent**: the single entry point.

## Deterministic Services

- **ACI Mock Core Payments API**: the real, callable mock backend implementation.

## API Contracts

- **ACI Mock Core Payments API**: Fully executable backends with an OpenAPI spec.
"""

    plan = parse_architecture_build_plan(document)

    assert plan is not None
    assert plan.other_components == (
        ("deterministic_service", "ACI Mock Core Payments API"),
        ("api_contract", "ACI Mock Core Payments API"),
    )


def test_api_contract_deferred_to_a_later_pass_is_not_treated_as_executable():
    """Regression guard for a real false positive: a catalog-stub
    contract's own text explicitly said "executable backends for these
    domains are deferred to later ... passes and must not be presented as
    functional in the first generated pass" - the word "executable" still
    appears, but in a negating context, so no deterministic_service
    should be derived for it."""

    document = """
## Multi-Agent Workflow

- **Ticket Classifier Agent**: classifies the incoming issue by category.
- **Support Triage Orchestrator Agent**: the single entry point.

## API Contracts

- **ACI Catalog Stub APIs (Domains 4-10)**: First-pass catalog entries only-name,
  description, documentation stub, and tags; executable backends for these
  domains are deferred to later Genie "Modernize and deliver" governed-refinement
  passes and must not be presented as functional in the first generated pass.
"""

    plan = parse_architecture_build_plan(document)

    assert plan is not None
    assert plan.other_components == (("api_contract", "ACI Catalog Stub APIs (Domains 4-10)"),)


def test_other_component_requirement_assignments_are_extracted_per_bullet():
    document = """
## Multi-Agent Workflow

- **Ticket Classifier Agent**: classifies the incoming issue by category.
- **Support Triage Orchestrator Agent**: the single entry point.

## Data Models

- **SandboxTenant**: the logical multi-tenant sandbox record (REQ-007).
- **Entitlement**: a product/API grant for one tenant (REQ-008, REQ-009).

## Gateway Policies

- **APIM JWT Policy**: validates bearer tokens with no cited requirement.
"""

    assignments = parse_component_requirement_assignments(document)

    assert assignments["sandboxtenant"] == ("REQ-007",)
    assert assignments["entitlement"] == ("REQ-008", "REQ-009")
    # No requirement id was cited in this bullet - absent, not an error,
    # matching the Orchestrator's own established behavior above.
    assert "apim jwt policy" not in assignments


def test_check_identity_requirement_coverage_is_none_when_requirements_name_no_identity_provider():
    """Most missions never mention a real identity provider at all - this
    must never force identity_config/gateway_policy onto them."""

    gap = check_identity_requirement_coverage(
        approved_requirements="[REQ-001] The system must be secure and log every action.",
        architecture_document="## Multi-Agent Workflow\n\n- **Orchestrator Agent**: coordinates.\n",
    )

    assert gap is None


def test_check_identity_requirement_coverage_flags_a_real_gap():
    """Regression test for a real, repeatedly observed incident: the same
    approved requirements and the same model, on two separate 'Validate
    your Vision' runs, did not reliably produce '## Identity
    Configuration'/'## Gateway Policies' even though the requirements
    explicitly named Microsoft Entra ID - prompt guidance alone is not
    sufficient for this security-relevant gap, so this deterministic
    check must fail it closed before Build ever starts."""

    gap = check_identity_requirement_coverage(
        approved_requirements=(
            "[REQ-006] A user authenticates through Microsoft Entra ID and the portal "
            "surfaces that user's identity throughout the journey."
        ),
        architecture_document="## Multi-Agent Workflow\n\n- **Orchestrator Agent**: coordinates.\n",
    )

    assert gap is not None
    assert "Identity Configuration" in gap
    assert "Gateway Policies" in gap


def test_check_identity_requirement_coverage_is_none_when_both_sections_present():
    gap = check_identity_requirement_coverage(
        approved_requirements="[REQ-006] A user authenticates through Microsoft Entra ID.",
        architecture_document=(
            "## Multi-Agent Workflow\n\n- **Orchestrator Agent**: coordinates.\n\n"
            "## Identity Configuration\n\n- **Entra ID Adapter**: real sign-in.\n\n"
            "## Gateway Policies\n\n- **APIM Policy**: enforces tokens.\n"
        ),
    )

    assert gap is None


def test_check_identity_requirement_coverage_flags_a_partial_gap():
    """Only one of the matched pair declared - still a real gap (an
    identity adapter with no gateway enforcement, or vice versa, is half
    a security control)."""

    gap = check_identity_requirement_coverage(
        approved_requirements="[REQ-006] Sign in via Microsoft Entra ID is required.",
        architecture_document=(
            "## Multi-Agent Workflow\n\n- **Orchestrator Agent**: coordinates.\n\n"
            "## Identity Configuration\n\n- **Entra ID Adapter**: real sign-in.\n"
        ),
    )

    assert gap is not None
    assert "Gateway Policies" in gap
    assert "Identity Configuration" not in gap


def test_parses_ui_pages_section_as_page_view_components():
    document = """
## Multi-Agent Workflow

- **Ticket Classifier Agent**: classifies the incoming issue by category.
- **Support Triage Orchestrator Agent**: the single entry point.

## UI Pages

- **Catalog Page**: browses the product catalog (REQ-010).
- **Dashboard Page**: shows the activation funnel (REQ-011).
"""

    plan = parse_architecture_build_plan(document)

    assert plan is not None
    assert plan.other_components == (
        ("page_view", "Catalog Page"),
        ("page_view", "Dashboard Page"),
    )
    assignments = parse_component_requirement_assignments(document)
    assert assignments["catalog page"] == ("REQ-010",)
    assert assignments["dashboard page"] == ("REQ-011",)

