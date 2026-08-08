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
