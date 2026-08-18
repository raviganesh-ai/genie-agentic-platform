"""Unit tests for `parse_architecture_build_plan`."""
from __future__ import annotations

from app.agents.tools.architecture_parsing import (
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

