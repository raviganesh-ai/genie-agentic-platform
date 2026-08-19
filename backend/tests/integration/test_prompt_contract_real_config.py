"""Integration test: pin the Multi-Agent Workflow output contract.

Loads the real repo config/prompts/registry.yaml (not a tmp_path fixture,
mirroring the pattern in tests/integration/test_foundry_agent_catalog_config.py)
and asserts the architecture-recommendation-v1 template still contains the
explicit instruction that each agent gets exactly one "**Name**:"-prefixed
bullet, never broken out into separate Fulfills/Inputs/Outputs/Handoffs
sub-bullets and never a bare sentence with no bold name/colon.

This exists because the Architecture Designer agent previously drifted
into emitting per-agent sub-bullets, which produced diagram/checkbox
noise in the Architecture Studio UI (one node per label instead of one
per agent - see frontend/src/utils/textArtifacts.ts's DETAIL_FIELD_LABEL
handling for the corresponding UI-side defense). A separate, later
regression let bullets omit the bold name/colon entirely (e.g. "- Package
Loader & Security Scanner Agent loads and parses..."), which the UI's
`splitIntoNamedSections` couldn't parse into cards at all, collapsing the
whole "Multi-Agent Workflow" section back into one unreadable prose blob
(frontend/src/utils/textArtifacts.ts gained a best-effort fallback parser
for that shape, but the prompt should still ask for the bold/colon format
by default). If this prompt is ever edited in a way that removes or
weakens either instruction, this test fails immediately instead of the
regression silently reappearing in production output.
"""
from __future__ import annotations

from pathlib import Path

from app.prompts.registry import PromptRegistry

_REPO_CONFIG_ROOT = Path(__file__).resolve().parents[3] / "config"


def test_architecture_recommendation_prompt_forbids_per_agent_sub_bullets():
    registry = PromptRegistry.load(_REPO_CONFIG_ROOT / "prompts")

    prompt = registry.get("architecture-recommendation-v1")
    template = prompt.template

    assert "one top-level bullet" in template
    assert '"**<Agent name>**:' in template
    assert "Never break an agent's responsibility" in template
    assert "nothing but exactly one bullet per agent" in template


def test_architecture_and_build_prompts_never_mandate_a_duplicate_live_agent_panel():
    """Regression guard for the "prototype looks pathetic" fix (see
    /memories/repo/deploy-backend.md): the generated mission shell now
    ALWAYS provides a live Agent Pipeline panel and a Mission Queue with
    real streamed output/downloads for every mission, with zero
    per-mission design work. If the architecture-design or build-generation
    prompts ever again instruct designing/generating a bespoke
    "Orchestrator Coordination Panel" or an "Output/Results Zone", every
    future mission's custom UI would go back to hand-rolling its own fake,
    never-updating duplicate of what the shell already does for real -
    exactly the regression this test exists to catch immediately.
    """
    registry = PromptRegistry.load(_REPO_CONFIG_ROOT / "prompts")

    for prompt_id in ("architecture-recommendation-v1", "build-generation-v1", "build-generation-component-v1"):
        # Normalize whitespace: this YAML's folded (">-") block scalars
        # preserve literal newlines for wrapped bullet-list continuation
        # lines (a pre-existing, harmless quirk of every prompt in this
        # file), so a phrase that happens to wrap across two source lines
        # would otherwise fail a naive substring check.
        template = " ".join(registry.get(prompt_id).template.split())
        assert "the orchestrator coordination panel should" not in template.lower()
        assert 'art of possibility" zone showing' not in template
        assert "displays completed outputs as agents" not in template.lower()

    build_v1 = " ".join(registry.get("build-generation-v1").template.split())
    assert "COMPONENT CONTRACT" in build_v1
    assert "onSubmit" in build_v1
    assert "never render your own output/progress/agent-status UI" in build_v1

    build_component_v1 = " ".join(registry.get("build-generation-component-v1").template.split())
    assert "COMPONENT CONTRACT" in build_component_v1
    assert "onSubmit" in build_component_v1


def test_control_selection_rules_are_explicit_and_consistent_across_ui_prompts():
    """Regression guard: the architecture and both build-generation prompts
    must give an explicit, deterministic rule mapping requirement shape to
    control type (dropdown vs. checkbox-group multi-select vs. slider vs.
    number vs. toggle vs. file picker vs. date vs. free text), rather than
    leaving control choice to the model's guesswork - this is what makes
    generated mission input forms consistent across missions instead of
    everything defaulting to a generic textarea.
    """
    registry = PromptRegistry.load(_REPO_CONFIG_ROOT / "prompts")

    architecture = " ".join(registry.get("architecture-recommendation-v1").template.split())
    assert "not by guesswork or habit" in architecture
    assert "multi-select" in architecture.lower()
    assert "a slider" in architecture.lower()

    build_v1 = " ".join(registry.get("build-generation-v1").template.split())
    assert "never by guesswork or default habit" in build_v1
    assert '`<select>`' in build_v1
    assert 'type="checkbox">` (multi-select' in build_v1
    assert 'type="range">`' in build_v1

    build_component_v1 = " ".join(registry.get("build-generation-component-v1").template.split())
    assert "never by guesswork or default habit" in build_component_v1
    assert '`<select>`' in build_component_v1
    assert 'type="checkbox">` (multi-select' in build_component_v1


def test_all_generation_prompts_preserve_every_approved_requirement_id():
    registry = PromptRegistry.load(_REPO_CONFIG_ROOT / "prompts")

    extraction = " ".join(registry.get("requirements-extraction-v1").template.split())
    assert "REQ-001" in extraction
    assert "Only the user may remove or defer" in extraction
    assert "implementation order" in extraction

    for prompt_id in (
        "architecture-recommendation-v1",
        "build-generation-v1",
        "build-generation-component-v1",
        "test-generation-v1",
    ):
        template = " ".join(registry.get(prompt_id).template.split())
        assert "every approved requirement ID" in template
        assert "Prototype status never authorizes omission" in template


def test_build_generation_prompts_require_self_verification_before_finishing():
    """Regression guard: the build-generation prompts must instruct the
    Build Agent to check its own requirement coverage and fix gaps before
    returning, rather than treating Deploy & Launch's bounded automatic
    repair budget as a substitute for a correct first pass (user directive:
    "don't make bad quality of code limitation as retry, 3 is enough if
    the code was generated to meet all the requirement... in the first
    round itself")."""
    registry = PromptRegistry.load(_REPO_CONFIG_ROOT / "prompts")

    build_v1 = " ".join(registry.get("build-generation-v1").template.split())
    assert "SELF-VERIFICATION" in build_v1
    assert "not as a substitute for doing a complete, correct job in this first pass" in build_v1

    build_component_v1 = " ".join(registry.get("build-generation-component-v1").template.split())
    assert "Before returning, re-check each requirement ID" in build_component_v1
    assert "rather than relying on that later repair budget to catch avoidable gaps" in build_component_v1
    assert "assigned_requirements" in build_component_v1
    assert "deterministically assigned to THIS component" in build_component_v1

