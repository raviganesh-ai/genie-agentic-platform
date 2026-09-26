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


def test_materialize_build_rejects_exact_uploaded_filename_gate():
    output = _SAMPLE_OUTPUT.replace(
        "export function MissionApp() {",
        """export function MissionApp() {
    const validateUpload = (file: File) => {
        if (file.name !== "blind_mqm_n30_package.json") {
            return "Please select the expected package";
        }
        return "";
    };""",
    )

    with pytest.raises(MaterializedCodeError, match="end-user-controlled filename"):
        materialize_build(output)


def test_materialize_build_allows_non_file_name_comparison():
    output = _SAMPLE_OUTPUT.replace(
        "export function MissionApp() {",
        'export function MissionApp() {\n  const isOrchestrator = agent.name === "orchestrator";',
    )

    build = materialize_build(output)

    assert build.ui_component is not None
    assert 'agent.name === "orchestrator"' in build.ui_component


def test_materialize_build_rejects_browser_side_uploaded_json_schema_gate():
    output = _SAMPLE_OUTPUT.replace(
        "export function MissionApp() {\n    return null;",
        """export function MissionApp() {
    const validatePacket = async (file: File) => {
        const packet = JSON.parse(await file.text());
        return Array.isArray(packet.documents) && packet.documents.length === 30;
    };
    return <input type=\"file\" />;""",
    )

    with pytest.raises(MaterializedCodeError, match="backend/orchestrator"):
        materialize_build(output)


def test_materialize_build_rejects_browser_side_wildcard_key_scan():
    output = _SAMPLE_OUTPUT.replace(
        "export function MissionApp() {\n    return null;",
        """export function MissionApp() {
    const containsKeyMaterial = (content: string) =>
        content.toLowerCase().includes("key");
    return <input type=\"file\" />;""",
    )

    with pytest.raises(MaterializedCodeError, match=r"broad '\*key\*' substring scan"):
        materialize_build(output)


def test_materialize_build_allows_non_upload_json_parsing():
    output = _SAMPLE_OUTPUT.replace(
        "export function MissionApp() {",
        """export function MissionApp() {
    const parseStructuredText = (value: string) => JSON.parse(value);""",
    )

    build = materialize_build(output)

    assert build.ui_component is not None
    assert "parseStructuredText" in build.ui_component


def test_materialize_build_rejects_interactive_ui_without_backend_handoff():
    output = _SAMPLE_OUTPUT.replace(
        "return null;",
        'return <input aria-label="Mission request" />;',
    )

    with pytest.raises(MaterializedCodeError, match="never calls its onSubmit prop"):
        materialize_build(output)


def test_materialize_build_rejects_file_input_without_attachment_handoff():
    output = _SAMPLE_OUTPUT.replace(
        "export function MissionApp() {\n    return null;",
        """export function MissionApp({ onSubmit }) {
    const handleFile = async (file: File) => {
        const content = await file.text();
        onSubmit(JSON.stringify({ filename: file.name }));
    };
    return <input type=\"file\" onChange={(event) => handleFile(event.target.files[0])} />;""",
    )

    with pytest.raises(MaterializedCodeError, match="does not pass an attachments array"):
        materialize_build(output)


def test_materialize_build_allows_file_input_handed_to_provisioned_backend():
    output = _SAMPLE_OUTPUT.replace(
        "export function MissionApp() {\n    return null;",
        """export function MissionApp({ onSubmit }) {
    const handleFile = async (file: File) => {
        const content = await file.text();
        const attachments = [{ name: file.name, content }];
        onSubmit(JSON.stringify({ filename: file.name }), attachments);
    };
    return <form className=\"genie-form\">
        <div className=\"genie-field genie-dropzone\">
            <input type=\"file\" style={{ display: \"none\" }} onChange={(event) => handleFile(event.target.files[0])} />
        </div>
    </form>;""",
    )

    build = materialize_build(output)

    assert build.ui_component is not None
    assert "attachments" in build.ui_component


def test_materialize_build_rejects_hidden_style_on_non_file_input():
    output = _SAMPLE_OUTPUT.replace(
        "return null;",
        'return <input type="text" style={{ display: "none" }} />;',
    )

    with pytest.raises(MaterializedCodeError, match="semantic HTML"):
        materialize_build(output)


def test_materialize_build_rejects_interactive_ui_without_shell_semantics():
    output = _SAMPLE_OUTPUT.replace(
        "export function MissionApp() {\n    return null;",
        """export function MissionApp({ onSubmit }) {
    const submit = () => onSubmit(JSON.stringify({ request: "review" }));
    return <form><input aria-label="Request" /><button onClick={submit}>Run</button></form>;""",
    )

    with pytest.raises(MaterializedCodeError, match="genie-form, genie-field, genie-btn"):
        materialize_build(output)


def test_materialize_build_rejects_multi_control_form_without_responsive_grid():
    output = _SAMPLE_OUTPUT.replace(
        "export function MissionApp() {\n    return null;",
        """export function MissionApp({ onSubmit }) {
    const submit = () => onSubmit(JSON.stringify({ first: "a", second: "b" }));
    return <form className=\"genie-form\">
        <label className=\"genie-field\"><input aria-label=\"First\" /></label>
        <label className=\"genie-field\"><input aria-label=\"Second\" /></label>
        <button className=\"genie-btn\" onClick={submit}>Run</button>
    </form>;""",
    )

    with pytest.raises(
        MaterializedCodeError,
        match="genie-form-section, genie-form-grid",
    ):
        materialize_build(output)


def test_materialize_build_rejects_generated_ui_direct_backend_invoke():
    output = _SAMPLE_OUTPUT.replace(
        "export function MissionApp() {",
        """export function MissionApp() {
    const run = () => fetch(`/invoke/stream`, { method: \"POST\" });""",
    )

    with pytest.raises(MaterializedCodeError, match="invoke endpoint directly"):
        materialize_build(output)


@pytest.mark.parametrize(
    "inline_style",
    [
        'style={{ color: "#555" }}',
        'style={{ padding: "1.5rem", maxWidth: 960 }}',
        'style={customPresentation}',
        'style="padding: 1.5rem"',
    ],
)
def test_materialize_build_rejects_inline_styles_that_override_shell_system(
    inline_style: str,
):
    output = _SAMPLE_OUTPUT.replace(
        "return null;",
        f"return <form {inline_style}>Mission input</form>;",
    )

    with pytest.raises(MaterializedCodeError, match="semantic HTML"):
        materialize_build(output)


def test_materialize_build_rejects_nested_submit_payload():
    # Observed live: the UI grouped fields under "evaluation_config" while the
    # orchestrator's flat `config.get("primary_model_id")` read silently found
    # nothing, surfacing as "Primary strong-model identifier is required".
    output = _SAMPLE_OUTPUT.replace(
        "export function MissionApp() {\n    return null;\n}",
        """export function MissionApp() {
    const handleSubmit = () => {
        const payload = {
            corpus_package: {filename: file.name, user_confirms_factory_package: confirmed},
            evaluation_config: {primary_model_id: primaryModel, peer_judge_model_ids: peers},
        };
        const message = JSON.stringify(payload);
        onSubmit(message, attachments);
    };
    return null;
}""",
    )

    with pytest.raises(MaterializedCodeError, match="nested objects"):
        materialize_build(output)


def test_materialize_build_allows_flat_submit_payload():
    output = _SAMPLE_OUTPUT.replace(
        "export function MissionApp() {\n    return null;\n}",
        """export function MissionApp() {
    const handleSubmit = () => {
        const payload = {
            filename: file.name,
            user_confirms_factory_package: confirmed,
            primary_model_id: primaryModel,
            peer_judge_model_ids: peers,
        };
        const message = JSON.stringify(payload);
        onSubmit(message, attachments);
    };
    return null;
}""",
    )

    build = materialize_build(output)

    assert build.ui_component is not None
    assert "primary_model_id" in build.ui_component


def test_materialize_build_allows_unrelated_nested_serialization():
    output = _SAMPLE_OUTPUT.replace(
        "export function MissionApp() {\n    return null;\n}",
        """export function MissionApp() {
    const preview = JSON.stringify({counts: {valid: 3, invalid: 0}});
    const message = JSON.stringify({primary_model_id: primaryModel});
    onSubmit(message);
    return null;
}""",
    )

    build = materialize_build(output)

    assert build.ui_component is not None
    assert "counts" in build.ui_component


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
    assert (tmp_path / "mission_foundry_runtime.py").exists()
    assert not (tmp_path / "token_validation.py").exists()
    main_source = (tmp_path / "main.py").read_text(encoding="utf-8")
    assert "acme-orchestrator" in main_source
    assert "authenticate_request" not in main_source
    assert "CORSMiddleware" not in main_source
    assert '@app.get("/health/ready")' in main_source
    assert "FOUNDRY_ORCHESTRATOR_AGENT_VERSION" in main_source
    assert "acme-requirements-specialist" in (tmp_path / "agent_config.py").read_text(encoding="utf-8")
    requirements = (tmp_path / "requirements.txt").read_text(encoding="utf-8")
    assert "httpx" not in requirements
    assert "pyjwt" not in requirements


def test_write_to_directory_routes_generated_foundry_imports_through_runtime(tmp_path: Path):
    output = '''
```python
# agent: Requirements Specialist
from agent_framework.foundry import FoundryAgent
agent = FoundryAgent("requirements-specialist")
```
```python
# agent: orchestrator
from agent_framework.foundry import FoundryAgent  # type: ignore
class OrchestratorAgent:
    pass
```
'''
    build = materialize_build(output)

    build.write_to_directory(
        tmp_path,
        backend_service_scaffold=generate_backend_service_scaffold(
            mission_title="Acme Mission",
            orchestrator_agent_name="acme-orchestrator",
            agent_foundry_names={"Requirements Specialist": "acme-requirements-specialist"},
        ),
    )

    specialist_source = (tmp_path / "agents" / "requirements_specialist.py").read_text(
        encoding="utf-8"
    )
    orchestrator_source = (tmp_path / "orchestrator.py").read_text(encoding="utf-8")
    assert "from mission_foundry_runtime import MissionFoundryAgent as FoundryAgent" in specialist_source
    assert "from mission_foundry_runtime import MissionFoundryAgent as FoundryAgent" in orchestrator_source
    assert "from agent_framework.foundry import FoundryAgent" not in specialist_source
    assert "from agent_framework.foundry import FoundryAgent" not in orchestrator_source


def test_mission_foundry_runtime_resolves_version_and_returns_dictionary_result():
    scaffold = generate_backend_service_scaffold(
        mission_title="Acme Mission",
        orchestrator_agent_name="acme-orchestrator",
        agent_foundry_names={"Requirements Specialist": "acme-requirements-specialist"},
    )
    runtime_module = types.ModuleType("mission_foundry_runtime_test")
    exec(  # noqa: S102 - executes Genie's own deterministic template in isolation.
        compile(scaffold["mission_foundry_runtime.py"], "mission_foundry_runtime.py", "exec"),
        runtime_module.__dict__,
    )
    captured = {}

    class _FakeCredential:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class _FakeAgents:
        async def get(self, agent_name):
            captured["resolved_name"] = agent_name
            return types.SimpleNamespace(
                versions=types.SimpleNamespace(
                    latest=types.SimpleNamespace(version="7")
                )
            )

    class _FakeProjectClient:
        def __init__(self, *, endpoint, credential):
            captured["endpoint"] = endpoint
            captured["credential"] = credential
            self.agents = _FakeAgents()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class _FakeSdkFoundryAgent:
        def __init__(self, **kwargs):
            captured["agent_kwargs"] = kwargs

        async def run(self, input_text, *, tools=None):
            captured["input_text"] = input_text
            captured["tools"] = tools
            return types.SimpleNamespace(text='{"verdict": "pass"}')

    runtime_module.DefaultAzureCredential = _FakeCredential
    runtime_module.AIProjectClient = _FakeProjectClient
    runtime_module._SdkFoundryAgent = _FakeSdkFoundryAgent

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("FOUNDRY_ENDPOINT", "https://foundry.example.com/projects/acme")
        result = asyncio.run(
            runtime_module.MissionFoundryAgent("acme-requirements-specialist").run(
                {"document_count": 30}
            )
        )

    assert result == {"verdict": "pass"}
    assert result.text == '{"verdict": "pass"}'
    assert captured["resolved_name"] == "acme-requirements-specialist"
    assert captured["agent_kwargs"]["agent_name"] == "acme-requirements-specialist"
    assert captured["agent_kwargs"]["agent_version"] == "7"
    assert json.loads(captured["input_text"]) == {"document_count": 30}


def test_backend_scaffold_has_no_prototype_authentication_runtime():
    scaffold = generate_backend_service_scaffold(
        mission_title="Local Mission",
        orchestrator_agent_name="orchestrator",
        agent_foundry_names={},
    )

    assert "authenticate_request" not in scaffold["main.py"]
    assert "token_validation.py" not in scaffold
    assert "PROTOTYPE_MISE_ENABLED" not in scaffold["main.py"]


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
    assert "orchestrator = OrchestratorAgent()" in main_source
    assert "await orchestrator.run(message, on_progress=on_progress)" in main_source
    # A direct conversational reply remains only as a fallback for requests
    # the real pipeline cannot accept (e.g. free-form chat).
    assert "_run_orchestrator_pipeline" in main_source
    assert "_conversational_reply" in main_source
    assert "OrchestratorAgent()" in main_source
    assert 'status_code=503' in main_source


def test_backend_scaffold_persists_upload_then_runs_orchestrator(
    monkeypatch, tmp_path: Path
):
    scaffold = generate_backend_service_scaffold(
        mission_title="Acme Mission",
        orchestrator_agent_name="acme-orchestrator",
        agent_foundry_names={"Requirements Specialist": "acme-requirements-specialist"},
    )
    fake_orchestrator_module = types.ModuleType("orchestrator")
    observed: dict[str, object] = {}

    class _FileReadingOrchestratorAgent:
        async def run(self, ui_message, on_progress=None):
            packet_path = tmp_path / "evaluation.json"
            observed["message"] = json.loads(ui_message)
            observed["content"] = packet_path.read_text(encoding="utf-8")
            return {"status": "processed", "bytes": len(observed["content"])}

    fake_orchestrator_module.OrchestratorAgent = _FileReadingOrchestratorAgent
    monkeypatch.setitem(sys.modules, "orchestrator", fake_orchestrator_module)
    monkeypatch.setenv("FACTORY_WORKING_DIR", str(tmp_path))

    generated_module = types.ModuleType("acme_main_attachment_handoff")
    monkeypatch.setitem(sys.modules, "acme_main_attachment_handoff", generated_module)
    exec(compile(scaffold["main.py"], "main.py", "exec"), generated_module.__dict__)  # noqa: S102
    request = generated_module.InvokeRequest(
        message=json.dumps({"run_id": "mqm-001"}),
        attachments=[
            generated_module.Attachment(
                name="../evaluation.json",
                content='{"items":[{"candidate_a":"A","candidate_b":"B"}]}',
            )
        ],
    )

    response = asyncio.run(generated_module.invoke(request))

    assert observed["message"] == {"run_id": "mqm-001"}
    assert observed["content"] == '{"items":[{"candidate_a":"A","candidate_b":"B"}]}'
    assert not (tmp_path.parent / "evaluation.json").exists()
    assert json.loads(response.output_text) == {"status": "processed", "bytes": 49}


def test_backend_scaffold_rejects_structured_request_when_constructor_fails(monkeypatch):
    """A structured generated-UI request must expose constructor failure.

    DerekPoC exposed this when generated code passed ``FoundryAgent`` a
    positional argument. Silently converting that failure into a generic
    conversational response made the broken prototype look successful.
    """
    scaffold = generate_backend_service_scaffold(
        mission_title="Acme Mission",
        orchestrator_agent_name="acme-orchestrator",
        agent_foundry_names={"Requirements Specialist": "acme-requirements-specialist"},
    )
    fake_orchestrator_module = types.ModuleType("orchestrator")

    class _BrokenOrchestratorAgent:
        def __init__(self):
            raise TypeError("FoundryAgent.__init__() takes 1 positional argument")

    fake_orchestrator_module.OrchestratorAgent = _BrokenOrchestratorAgent
    monkeypatch.setitem(sys.modules, "orchestrator", fake_orchestrator_module)

    generated_module = types.ModuleType("acme_main_constructor_failure")
    exec(compile(scaffold["main.py"], "main.py", "exec"), generated_module.__dict__)  # noqa: S102

    with pytest.raises(TypeError, match="FoundryAgent"):
        asyncio.run(generated_module._run_orchestrator_pipeline("{}"))


def test_backend_scaffold_keeps_conversational_fallback_for_plain_text(monkeypatch):
    scaffold = generate_backend_service_scaffold(
        mission_title="Acme Mission",
        orchestrator_agent_name="acme-orchestrator",
        agent_foundry_names={"Requirements Specialist": "acme-requirements-specialist"},
    )
    fake_orchestrator_module = types.ModuleType("orchestrator")

    class _StructuredOnlyOrchestratorAgent:
        async def run(self, _ui_message, on_progress=None):
            raise ValueError("Expected the generated UI's JSON payload")

    fake_orchestrator_module.OrchestratorAgent = _StructuredOnlyOrchestratorAgent
    monkeypatch.setitem(sys.modules, "orchestrator", fake_orchestrator_module)
    generated_module = types.ModuleType("acme_main_plain_text_fallback")
    exec(compile(scaffold["main.py"], "main.py", "exec"), generated_module.__dict__)  # noqa: S102

    result = asyncio.run(generated_module._run_orchestrator_pipeline("quick question"))

    assert result is None


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
    fake_token_validation_module = types.ModuleType("token_validation")
    fake_token_validation_module.authenticate_request = object()
    monkeypatch.setitem(sys.modules, "token_validation", fake_token_validation_module)

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
