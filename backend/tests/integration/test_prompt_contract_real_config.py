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


def test_discovery_deep_dive_prompt_bounds_structured_output() -> None:
    registry = PromptRegistry.load(_REPO_CONFIG_ROOT / "prompts")

    prompt = registry.get("discovery-persona-deep-dive-v1")
    template = " ".join(prompt.template.split())

    assert "under 12,000 characters" in template
    assert "at most 10 deep_dive_findings" in template
    assert "at most 8 questions" in template
    assert "{retry_instruction}" in prompt.template


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


def test_ui_prompts_forbid_binding_file_uploads_to_one_exact_literal_name():
    """Regression guard for a live "blind MQM n30" mission whose generated
    Mission Input form required a file uploaded exactly named
    "blind_mqm_n30_package.json" - the requirement's own example filename
    was taken as a strict validation contract, so any real file a tester
    actually had was rejected and the submit button stayed disabled
    forever, making the prototype untestable. Both UI-generation prompts
    must instruct the Build Agent to describe/accept an uploaded file by
    its general TYPE (extension/MIME), never by one exact literal name, in
    both validation logic and visible label/help copy.
    """
    registry = PromptRegistry.load(_REPO_CONFIG_ROOT / "prompts")

    for prompt_id in ("build-generation-v1", "build-generation-component-v1"):
        template = " ".join(registry.get(prompt_id).template.split())
        assert "FILE UPLOAD VALIDATION" in template
        assert "never one exact literal file, name, or path" in template
        assert "never a strict filename contract" in template
        assert "accept` attribute to that type's extension/MIME list" in template


def test_orchestrator_prompts_require_live_on_progress_hand_off_narration():
    """Regression guard: a live "blind MQM" mission's Agent Pipeline panel
    never visibly animated - the generated Orchestrator's `run()` awaited
    the entire multi-agent pipeline in one blocking call and only returned
    its final result at the very end, so the mission UI received zero
    signal while real work was happening and the panel just flashed from
    all-pending to all-complete once everything was already done. Both
    orchestrator-generation prompts must require an `on_progress` callback
    parameter that is awaited with each specialist agent's own exact name
    immediately before and after that agent is called, so the deployed
    backend (see app.deploy_launch.code_materializer._stream_agent_response)
    can relay real hand-off narration to the browser AS IT HAPPENS.
    """
    registry = PromptRegistry.load(_REPO_CONFIG_ROOT / "prompts")

    for prompt_id in ("build-generation-v1", "build-generation-component-v1"):
        template = " ".join(registry.get(prompt_id).template.split())
        assert "on_progress: Callable[[str], Awaitable[None]] | None = None" in template
        assert "LIVE HAND-OFF NARRATION" in template
        assert "exactly as it appears in the" in template.lower() or "EXACTLY as it appears in the" in template
        assert "never call `on_progress` for the orchestrator itself" in template.lower()


def test_orchestrator_prompts_require_reporting_coverage_gaps_against_enumerated_requirements():
    """Regression guard for a live REQ-025 "seven target languages" mission
    (Korean, German, Spanish, Italian, French, Simplified Chinese,
    Japanese): a real test run's uploaded package only actually contained
    Spanish and Polish documents (Polish is not even one of the seven
    required languages), yet the mission's output reported the run as a
    plain success with no indication that 5 of 7 required languages were
    never evaluated and one out-of-scope language was. Both
    orchestrator-generation prompts must require the returned result to
    report required vs. covered vs. missing/unexpected items whenever the
    approved requirements enumerate a specific finite list to cover, so
    partial/off-scope coverage is visible in the output instead of
    silently indistinguishable from full success.
    """
    registry = PromptRegistry.load(_REPO_CONFIG_ROOT / "prompts")

    for prompt_id in ("build-generation-v1", "build-generation-component-v1"):
        template = " ".join(registry.get(prompt_id).template.split())
        assert "COVERAGE VALIDATION" in template
        assert '"required_items"' in template
        assert '"covered_items"' in template
        assert '"missing_items"' in template
        assert '"unexpected_items"' in template


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


def test_acceptance_test_prompt_uses_trusted_auth_proxy_without_disclosing_token():
    registry = PromptRegistry.load(_REPO_CONFIG_ROOT / "prompts")

    template = " ".join(registry.get("test-generation-v1").template.split())

    assert "trusted acceptance-test proxy" in template
    assert "receive no access token" in template
    assert "MISSION_UNAUTHENTICATED_BACKEND_URL" in template


def test_generation_prompts_fail_closed_on_ui_orchestrator_schema_drift():
    registry = PromptRegistry.load(_REPO_CONFIG_ROOT / "prompts")

    for prompt_id in ("build-generation-v1", "build-generation-component-v1"):
        template = " ".join(registry.get(prompt_id).template.split())
        assert "one flat JSON" in template
        assert "exact same key names and casing" in template
        assert "zone/group nesting" in template

    test_generation = " ".join(
        registry.get("test-generation-v1").template.split()
    )
    assert "exact JSON object assembled by the generated UI" in test_generation
    assert "mission-specific result fields" in test_generation
    assert "HTTP 200 alone" in test_generation


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


def test_ui_generation_and_regeneration_prompts_apply_impeccable_design_contract():
    registry = PromptRegistry.load(_REPO_CONFIG_ROOT / "prompts")

    for prompt_id in ("build-generation-v1", "build-generation-component-v1"):
        template = " ".join(registry.get(prompt_id).template.split())
        assert "IMPECCABLE DESIGN CONTRACT" in template
        assert "https://impeccable.style/" in template
        assert "never wrap every element in a card or nest cards" in template
        assert "pinned Impeccable detector" in template
        assert "reduced-motion" in template or "reduced motion" in template

    regeneration = " ".join(
        registry.get("build-component-regeneration-v1").template.split()
    )
    assert 'If component_kind is "ui"' in regeneration
    assert "https://impeccable.style/" in regeneration
    assert "pinned Impeccable detector" in regeneration


def test_architecture_prompt_shapes_an_impeccable_visual_direction_for_each_mission():
    registry = PromptRegistry.load(_REPO_CONFIG_ROOT / "prompts")
    template = " ".join(registry.get("architecture-recommendation-v1").template.split())

    assert "shape-first method from https://impeccable.style/" in template
    assert "OPERATE (fast scanning and repeated action)" in template
    assert '"Surface mode: <OPERATE|READ|EXPERIENCE|PERSUADE>.' in template
    assert "do not add a third top-level section" in template

