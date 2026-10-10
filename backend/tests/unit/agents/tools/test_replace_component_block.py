"""Unit tests for ``orchestration_tools.replace_component_block``.

Covers the persistence half of the Workshop "Regenerate" fix: splicing one
freshly regenerated component's fenced code block back into a
``build-solution`` step's full ``output_text`` blob, matched by the same
``# agent: <name>``/``// agent: <name>`` label convention every component
already follows.
"""
from __future__ import annotations

import pytest

from app.agents.tools.orchestration_tools import replace_component_block

ORIGINAL_BUILD_OUTPUT = """\
```python
# agent: Catalog Discovery Agent
def run():
    return "old catalog code"
```

```python
# agent: orchestrator
class OrchestratorAgent:
    def __init__(self):
        pass
```

```tsx
// agent: page:Landing page
export default function LandingPage() {
    return null;
}
```
"""


def test_replace_component_block_replaces_only_the_named_component() -> None:
    new_orchestrator_block = (
        "```python\n"
        "# agent: orchestrator\n"
        "class OrchestratorAgent:\n"
        "    def __init__(self):\n"
        "        self.catalog_tool = agent_framework.FunctionTool(\n"
        "            name=\"Catalog Discovery Agent\",\n"
        "        )\n"
        "```"
    )

    updated = replace_component_block(
        ORIGINAL_BUILD_OUTPUT, "orchestrator", new_orchestrator_block
    )

    assert "FunctionTool(\n            name=" in updated
    # Every other component's block must be untouched.
    assert '# agent: Catalog Discovery Agent' in updated
    assert 'return "old catalog code"' in updated
    assert "// agent: page:Landing page" in updated
    assert "export default function LandingPage" in updated
    # The old, buggy orchestrator body must be gone.
    assert "self.catalog_tool = agent_framework.FunctionTool(\n            )" not in updated


def test_replace_component_block_matches_page_label_with_or_without_prefix() -> None:
    new_page_block = (
        "```tsx\n// agent: page:Landing page\nexport default function LandingPage() {\n"
        '    return "fixed";\n}\n```'
    )

    # Caller may pass either the raw captured label ("page:Landing page")
    # or just the bare display name ("Landing page") - both must match the
    # same block.
    updated_with_prefix = replace_component_block(
        ORIGINAL_BUILD_OUTPUT, "page:Landing page", new_page_block
    )
    updated_without_prefix = replace_component_block(
        ORIGINAL_BUILD_OUTPUT, "Landing page", new_page_block
    )

    for updated in (updated_with_prefix, updated_without_prefix):
        assert 'return "fixed";' in updated
        assert "# agent: orchestrator" in updated
        assert "# agent: Catalog Discovery Agent" in updated


def test_replace_component_block_raises_for_unknown_label() -> None:
    with pytest.raises(ValueError, match="No existing component labeled"):
        replace_component_block(
            ORIGINAL_BUILD_OUTPUT,
            "Nonexistent Agent",
            "```python\n# agent: Nonexistent Agent\npass\n```",
        )
