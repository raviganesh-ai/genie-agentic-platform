"""End-to-end wiring check for one materialized mission's generated backend.

Boots a real, materialized mission backend (the exact ``main.py`` +
``orchestrator.py`` templates ``BackendDeploymentService`` ships to Azure
Container Apps) in-process - no Azure, no Docker - and drives it through
FastAPI's own TestClient. This exists because per-template unit tests (in
``tests/unit/deploy_launch/``) validate the backend template and the
frontend template in isolation, but neither one alone can catch a drift
between what the backend actually emits and what the generated mission
frontend's ``MissionConsole.runItem()`` actually parses (exactly the class
of bug a relative-path template typo or an SSE frame shape change would
be) - this test exercises the real contract between them.
"""
from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.deploy_launch.code_materializer import (
    generate_backend_service_scaffold,
    materialize_build,
)

_BUILD_OUTPUT = """
```python
# agent: Requirements Specialist
async def run() -> None:
    pass
```

```python
# agent: orchestrator
from agent_config import AGENT_FOUNDRY_NAMES
from agent_framework import FunctionTool
from mission_foundry_runtime import MissionFoundryAgent


class OrchestratorAgent:
    async def run(self, ui_message: str, on_progress=None):
        specialist = MissionFoundryAgent(
            agent_name=AGENT_FOUNDRY_NAMES["Requirements Specialist"]
        )
        tool = FunctionTool(name="requirements", func=specialist.run)
        if on_progress:
            await on_progress("Handing off to Requirements Specialist...")
            result = await specialist.run(ui_message)
            await on_progress("Requirements Specialist completed.")
        return {"result": str(result), "tool": str(tool)}
```

```tsx
// agent: ui
export function MissionApp({ onSubmit }) {
    return <button className="genie-btn" onClick={() => onSubmit(JSON.stringify({ request: "run" }))}>Run</button>;
}
```
"""

_GENERATED_MODULE_NAMES = ("main", "orchestrator", "mission_foundry_runtime", "agent_config")


@pytest.fixture()
def generated_mission_app(tmp_path: Path) -> Iterator[object]:
    """Materializes a real build and imports its generated ``main.py`` live."""
    build = materialize_build(_BUILD_OUTPUT)
    scaffold = generate_backend_service_scaffold(
        mission_title="Wiring Check Mission",
        orchestrator_agent_name="wiring-check-orchestrator",
        agent_foundry_names={"Requirements Specialist": "wiring-check-requirements-specialist"},
    )
    build.write_to_directory(tmp_path, backend_service_scaffold=scaffold)

    sys.path.insert(0, str(tmp_path))
    for stale in _GENERATED_MODULE_NAMES:
        sys.modules.pop(stale, None)
    try:
        # The real mission_foundry_runtime.py talks to live Azure AI Foundry -
        # patch its one network-calling method so this test stays offline
        # while still exercising the real, generated orchestrator/main.py.
        mission_foundry_runtime = importlib.import_module("mission_foundry_runtime")

        async def _fake_specialist_run(self: object, messages: object, **_kwargs: object) -> dict:
            return {"echo": messages}

        mission_foundry_runtime.MissionFoundryAgent.run = _fake_specialist_run

        main_module = importlib.import_module("main")
        yield main_module.app
    finally:
        sys.path.remove(str(tmp_path))
        for stale in _GENERATED_MODULE_NAMES:
            sys.modules.pop(stale, None)


def test_generated_backend_serves_health_and_readiness(generated_mission_app: object) -> None:
    with TestClient(generated_mission_app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/health/ready").json() == {"status": "ready"}


def test_generated_backend_invoke_stream_matches_frontend_sse_contract(
    generated_mission_app: object,
) -> None:
    """Mirrors exactly how the generated mission frontend's
    ``MissionConsole.runItem()`` parses the response: split the raw body on
    ``"\\n\\n"``, strip a leading ``"data: "`` prefix from each frame, and
    JSON-decode it looking for ``progress``/``delta``/``done`` keys. If this
    contract ever drifts between the backend's ``_MAIN_PY_TEMPLATE`` and the
    frontend's Mission Console template, this test fails without needing a
    real browser or real Azure infrastructure.
    """
    with TestClient(generated_mission_app) as client:
        response = client.post(
            "/invoke/stream",
            json={"message": "hello mission", "attachments": []},
        )

    assert response.status_code == 200
    frames = [
        json.loads(payload)
        for frame in response.text.split("\n\n")
        if (payload := frame.removeprefix("data: ").strip())
    ]

    progress_frames = [frame for frame in frames if "progress" in frame]
    done_frames = [frame for frame in frames if frame.get("done")]

    assert [frame["progress"] for frame in progress_frames] == [
        "Handing off to Requirements Specialist...",
        "Requirements Specialist completed.",
    ]
    assert len(done_frames) == 1
    output = json.loads(done_frames[0]["output_text"])
    assert output["result"] == str({"echo": "hello mission"})
