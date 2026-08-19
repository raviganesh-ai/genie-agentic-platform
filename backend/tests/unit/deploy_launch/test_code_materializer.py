"""Unit tests for materialize_build (code_materializer)."""
from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path

import pytest

from app.deploy_launch.code_materializer import (
    MaterializedCodeError,
    generate_agent_config_module,
    generate_backend_service_scaffold,
    materialize_build,
)

_SAMPLE_OUTPUT = '''
Some narrative text before the code.

```python
# agent: Requirements Specialist
async def run() -> None:
    pass
```

```python
# agent: orchestrator
class OrchestratorAgent:
    async def run(self, ui_message: str) -> None:
        pass
```

```tsx
// agent: ui
export function MissionApp() {
    return null;
}
```
'''


def test_materialize_build_parses_all_three_pieces():
    build = materialize_build(_SAMPLE_OUTPUT)

    assert "Requirements Specialist" in build.agent_modules
    assert "async def run" in build.agent_modules["Requirements Specialist"]
    assert build.orchestrator_module is not None
    assert "class OrchestratorAgent" in build.orchestrator_module
    assert build.ui_component is not None
    assert "MissionApp" in build.ui_component


def test_materialize_build_raises_when_no_code_blocks_found():
    with pytest.raises(MaterializedCodeError):
        materialize_build("no code here at all")


def test_materialize_build_raises_when_orchestrator_class_is_misnamed():
    # main.py's deterministic scaffold always does
    # 'from orchestrator import OrchestratorAgent' - any other class name
    # would silently deploy a mission whose real pipeline is unreachable.
    misnamed_output = '''
```python
# agent: orchestrator
class FactoryOrchestratorAgent:
    async def run(self, ui_message: str) -> None:
        pass
```
'''
    with pytest.raises(MaterializedCodeError, match="OrchestratorAgent"):
        materialize_build(misnamed_output)


def test_write_to_directory_creates_expected_files(tmp_path: Path):
    build = materialize_build(_SAMPLE_OUTPUT)

    written = build.write_to_directory(tmp_path)

    assert (tmp_path / "agents" / "requirements_specialist.py").exists()
    assert (tmp_path / "orchestrator.py").exists()
    assert (tmp_path / "MissionApp.tsx").exists()
    assert len(written) == 3


def test_write_to_directory_includes_backend_service_scaffold(tmp_path: Path):
    build = materialize_build(_SAMPLE_OUTPUT)
    scaffold = generate_backend_service_scaffold(
        mission_title="Acme Mission",
        orchestrator_agent_name="acme-orchestrator",
        agent_foundry_names={"Requirements Specialist": "acme-requirements-specialist"},
    )

    build.write_to_directory(tmp_path, backend_service_scaffold=scaffold)

    assert (tmp_path / "main.py").exists()
    assert (tmp_path / "Dockerfile").exists()
    assert (tmp_path / "requirements.txt").exists()
    assert (tmp_path / "agent_config.py").exists()
    main_source = (tmp_path / "main.py").read_text(encoding="utf-8")
    assert "acme-orchestrator" in main_source
    assert "CORSMiddleware" in main_source
    assert "FOUNDRY_ORCHESTRATOR_AGENT_VERSION" in main_source
    assert "acme-requirements-specialist" in (tmp_path / "agent_config.py").read_text(encoding="utf-8")


def test_backend_service_scaffold_main_py_is_valid_python_and_supports_attachments():
    scaffold = generate_backend_service_scaffold(
        mission_title="Acme Mission",
        orchestrator_agent_name="acme-orchestrator",
        agent_foundry_names={"Requirements Specialist": "acme-requirements-specialist"},
    )
    main_source = scaffold["main.py"]

    compile(main_source, "main.py", "exec")  # never emits an unterminated/garbled literal
    assert "class Attachment(BaseModel):" in main_source
    assert "attachments: list[Attachment] = []" in main_source
    assert "_MAX_ATTACHMENT_CHARS" in main_source
    assert "status_code=413" in main_source
    # The escape sequence must survive as a literal 2-char "\n" in the
    # generated f-string, never a real newline (see 2026-08-17 incident).
    assert '"--- Attached file: {attachment.name} ---\\n{attachment.content}"' in main_source


def test_backend_service_scaffold_main_py_runs_the_real_orchestrator_pipeline():
    """Regression guard for the "big miss" incident (2026-08-17): the
    deployed backend proxy must persist uploaded attachments to disk in
    full and actually invoke this mission's own generated ``OrchestratorAgent``
    pipeline - never only fold uploads into a single chat turn sent
    straight to a conversational Foundry agent, which silently drops most
    of an uploaded requirement package instead of processing it in full.
    """
    scaffold = generate_backend_service_scaffold(
        mission_title="Acme Mission",
        orchestrator_agent_name="acme-orchestrator",
        agent_foundry_names={"Requirements Specialist": "acme-requirements-specialist"},
    )
    main_source = scaffold["main.py"]

    compile(main_source, "main.py", "exec")
    # Every uploaded attachment's full content is written to disk (never
    # truncated/summarized) so the real pipeline can read every requirement.
    assert "def _persist_attachments(" in main_source
    assert "FACTORY_WORKING_DIR" in main_source
    assert "write_text(attachment.content" in main_source
    # The real generated Orchestrator (orchestrator.py, sibling module) is
    # imported and actually invoked - not bypassed.
    assert "from orchestrator import OrchestratorAgent" in main_source
    assert "await OrchestratorAgent().run(message, on_progress=on_progress)" in main_source
    # A direct conversational reply remains only as a fallback for requests
    # the real pipeline cannot accept (e.g. free-form chat).
    assert "_run_orchestrator_pipeline" in main_source
    assert "_conversational_reply" in main_source


def test_generate_agent_config_module_embeds_the_real_agent_foundry_name_mapping():
    module_source = generate_agent_config_module(
        {"Requirements Specialist": "acme-requirements-specialist", "orchestrator": "acme-orchestrator"}
    )

    assert "AGENT_FOUNDRY_NAMES" in module_source
    assert "'Requirements Specialist': 'acme-requirements-specialist'" in module_source
    assert "'orchestrator': 'acme-orchestrator'" in module_source


def test_stream_agent_response_relays_on_progress_narration_as_it_happens(monkeypatch):
    """Regression guard for the "Agent Pipeline never animates" incident: a
    real orchestrator pipeline run used to be awaited to completion before
    ANY event reached the browser (a single delta immediately followed by
    done), so the mission UI's live pipeline visualization never had a
    chance to light up node-by-node while specialist agents were actually
    working. The generated backend proxy must relay each ``on_progress``
    narration call as its OWN SSE delta event in real time, with the
    pipeline's final structured result arriving only in the closing
    "done" event.
    """
    scaffold = generate_backend_service_scaffold(
        mission_title="Acme Mission",
        orchestrator_agent_name="acme-orchestrator",
        agent_foundry_names={"Requirements Specialist": "acme-requirements-specialist"},
    )
    main_source = scaffold["main.py"]

    fake_orchestrator_module = types.ModuleType("orchestrator")

    class _FakeOrchestratorAgent:
        async def run(self, ui_message, on_progress=None):
            if on_progress is not None:
                await on_progress("Handing off to Requirements Specialist...")
                await on_progress("Requirements Specialist completed.")
            return {"summary": "done", "requirement_count": 3}

    fake_orchestrator_module.OrchestratorAgent = _FakeOrchestratorAgent
    monkeypatch.setitem(sys.modules, "orchestrator", fake_orchestrator_module)

    generated_module = types.ModuleType("acme_main")
    monkeypatch.setitem(sys.modules, "acme_main", generated_module)
    # Deliberate: this is our own deterministically generated template
    # source (never user/agent input), executed in an isolated throwaway
    # module purely to exercise the real _stream_agent_response behavior.
    exec(compile(main_source, "main.py", "exec"), generated_module.__dict__)  # noqa: S102

    invoke_request_cls = generated_module.InvokeRequest
    stream_agent_response = generated_module._stream_agent_response

    async def _collect_events() -> list[dict[str, object]]:
        request = invoke_request_cls(message="{}", attachments=[])
        return [
            json.loads(event.removeprefix("data: ").strip())
            async for event in stream_agent_response(request)
        ]

    events = asyncio.run(_collect_events())

    assert events[0] == {"delta": "Handing off to Requirements Specialist..."}
    assert events[1] == {"delta": "Requirements Specialist completed."}
    assert events[-1]["done"] is True
    assert json.loads(events[-1]["output_text"]) == {"summary": "done", "requirement_count": 3}
