"""Integration test: pin the Multi-Agent Workflow output contract.

Loads the real repo config/prompts/registry.yaml (not a tmp_path fixture,
mirroring the pattern in tests/integration/test_foundry_agent_catalog_config.py)
and asserts the architecture-recommendation-v1 template still contains the
explicit instruction that each agent gets exactly one flowing-sentence
bullet, never broken out into separate Fulfills/Inputs/Outputs/Handoffs
sub-bullets.

This exists because the Architecture Designer agent previously drifted
into emitting per-agent sub-bullets, which produced diagram/checkbox
noise in the Architecture Studio UI (one node per label instead of one
per agent - see frontend/src/utils/textArtifacts.ts's DETAIL_FIELD_LABEL
handling for the corresponding UI-side defense). If this prompt is ever
edited in a way that removes or weakens that instruction, this test
fails immediately instead of the regression silently reappearing in
production output.
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
    assert "single flowing sentence" in template
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
