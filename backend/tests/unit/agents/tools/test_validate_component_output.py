"""Unit tests for ``validate_component_output`` - the shared gate the original
per-component build path and Workshop's "Regenerate" action both run before
persisting a component's generated code."""
from __future__ import annotations

import pytest

from app.agents.tool_execution import ToolExecutionError
from app.agents.tools.orchestration_tools import validate_component_output


def test_validate_component_output_accepts_a_clean_deterministic_service():
    output_text = (
        "```python\n"
        "# agent: service:Example Service\n"
        "from pathlib import Path\n"
        "from fastapi import APIRouter, Path as ApiPath\n"
        "router = APIRouter()\n"
        "DATA_DIR = Path('/data')\n"
        "\n"
        "@router.get('/v1/items/{item_id}')\n"
        "async def get_item(item_id: str = ApiPath(...)):\n"
        "    return {'item_id': item_id}\n"
        "```"
    )

    validate_component_output(
        output_text, component_kind="deterministic_service", marker_name="service:Example Service"
    )


def test_validate_component_output_rejects_unaliased_pathlib_and_fastapi_path_collision():
    """Regression test for a real, observed incident: a deterministic service
    imported bare `Path` from both `pathlib` and `fastapi` - the fastapi
    import silently shadowed pathlib's, so `Path(os.getenv("DATA_DIR", ...))`
    actually called FastAPI's route-parameter function and raised at import
    time. The module's router then failed to import - service_mounts.py only
    logs that failure, never raises - so every one of its routes silently
    404d with no startup error anywhere."""

    output_text = (
        "```python\n"
        "# agent: service:Onboarding Workflow Service\n"
        "import os\n"
        "from pathlib import Path\n"
        "from fastapi import APIRouter, Body, Query, Path, HTTPException, status\n"
        "router = APIRouter()\n"
        "DATA_DIR = Path(os.getenv('DATA_DIR', '/data'))\n"
        "```"
    )

    with pytest.raises(ToolExecutionError, match="imports 'Path' unaliased"):
        validate_component_output(
            output_text,
            component_kind="deterministic_service",
            marker_name="service:Onboarding Workflow Service",
        )


def test_validate_component_output_allows_pathlib_path_alone():
    output_text = (
        "```python\n"
        "# agent: service:Example Service\n"
        "from pathlib import Path\n"
        "DATA_DIR = Path('/data')\n"
        "```"
    )

    validate_component_output(
        output_text, component_kind="deterministic_service", marker_name="service:Example Service"
    )


def test_validate_component_output_allows_fastapi_path_alone():
    output_text = (
        "```python\n"
        "# agent: service:Example Service\n"
        "from fastapi import APIRouter, Path\n"
        "router = APIRouter()\n"
        "```"
    )

    validate_component_output(
        output_text, component_kind="deterministic_service", marker_name="service:Example Service"
    )


def test_validate_component_output_ignores_the_collision_pattern_for_other_kinds():
    """The collision check is specific to deterministic_service - an agent
    module importing both names (for whatever reason) is not rejected by
    this particular gate."""

    output_text = (
        "```python\n"
        "# agent: Some Agent\n"
        "from pathlib import Path\n"
        "from fastapi import Path\n"
        "```"
    )

    validate_component_output(output_text, component_kind="agent", marker_name="Some Agent")
