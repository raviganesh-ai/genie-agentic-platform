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
    generate_models_init,
    generate_routing_shell,
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
    async def run(self, ui_message: str, on_progress=None) -> None:
        if on_progress is not None:
            await on_progress("Handing off to Requirements Specialist...")
            await on_progress("Requirements Specialist completed.")
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


def test_materialize_build_rejects_nonexistent_asyncio_random_type():
    output = _SAMPLE_OUTPUT.replace(
        "class OrchestratorAgent:",
        "class OrchestratorAgent:\n    def _deterministic_rng(self) -> asyncio.Random:\n        pass",
    )

    with pytest.raises(MaterializedCodeError, match="asyncio.Random"):
        materialize_build(output)


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


@pytest.mark.parametrize(
    "source",
    (
        "const isFileValid = parsedDocCount === 30;",
        "if (docsArray.length !== 30) { return null; }",
    ),
)
def test_materialize_build_rejects_exact_ui_sample_cardinality_gate(source: str):
    output = _SAMPLE_OUTPUT.replace(
        "export function MissionApp() {",
        f"export function MissionApp() {{\n    {source}",
    )

    with pytest.raises(MaterializedCodeError, match="representative sample"):
        materialize_build(output)


def test_materialize_build_allows_exact_count_comparison_for_coverage_warning():
    output = _SAMPLE_OUTPUT.replace(
        "export function MissionApp() {",
        "export function MissionApp() {\n"
        "    if (parsedDocCount !== 30) {\n"
        "        setCoverageWarning('This run uses a partial representative sample.');\n"
        "    }",
    )

    build = materialize_build(output)

    assert build.ui_component is not None


def test_materialize_build_rejects_exact_orchestrator_sample_cardinality_gate():
    output = _SAMPLE_OUTPUT.replace(
        "class OrchestratorAgent:",
        "document_count = 7\nif document_count != 30:\n    raise ValueError('wrong count')\n\nclass OrchestratorAgent:",
    )

    with pytest.raises(MaterializedCodeError, match="coverage evidence"):
        materialize_build(output)


def test_materialize_build_allows_non_empty_sample_validation():
    output = _SAMPLE_OUTPUT.replace(
        "export function MissionApp() {",
        "export function MissionApp() {\n    const isFileValid = parsedDocCount > 0;",
    ).replace(
        "class OrchestratorAgent:",
        "document_count = 7\nif document_count < 1:\n    raise ValueError('empty')\n\nclass OrchestratorAgent:",
    )

    build = materialize_build(output)

    assert build.ui_component is not None


def test_materialize_build_allows_missing_specialist_progress_narration():
    output = _SAMPLE_OUTPUT.replace(
        'await on_progress("Requirements Specialist completed.")',
        'await on_progress("Pipeline phase completed.")',
    )

    build = materialize_build(output)

    assert build.orchestrator_module is not None


def test_materialize_build_allows_unique_base_name_progress_for_qualified_agent():
    output = _SAMPLE_OUTPUT.replace(
        "# agent: Requirements Specialist",
        "# agent: Requirements Specialist (Primary Reviewer)",
    )

    build = materialize_build(output)

    assert "Requirements Specialist (Primary Reviewer)" in build.agent_modules


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
    async def run(self, ui_message, on_progress=None):
        await on_progress("Handing off to Requirements Specialist...")
        await on_progress("Requirements Specialist completed.")
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


def test_backend_scaffold_falls_back_when_generated_pipeline_glue_fails(monkeypatch):
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

    result = asyncio.run(generated_module._run_orchestrator_pipeline("{}"))

    assert result is None


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


def test_backend_stream_returns_fallback_output_when_generated_pipeline_glue_fails(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("FACTORY_WORKING_DIR", str(tmp_path))
    scaffold = generate_backend_service_scaffold(
        mission_title="Acme Mission",
        orchestrator_agent_name="acme-orchestrator",
        agent_foundry_names={"Requirements Specialist": "acme-requirements-specialist"},
    )
    fake_orchestrator_module = types.ModuleType("orchestrator")

    class _MismatchedOrchestratorAgent:
        async def run(self, _ui_message, on_progress=None):
            raise ValueError("Run ID / Output directory name is required.")

    fake_orchestrator_module.OrchestratorAgent = _MismatchedOrchestratorAgent
    monkeypatch.setitem(sys.modules, "orchestrator", fake_orchestrator_module)
    generated_module = types.ModuleType("acme_main_fallback_stream")
    monkeypatch.setitem(sys.modules, "acme_main_fallback_stream", generated_module)
    exec(compile(scaffold["main.py"], "main.py", "exec"), generated_module.__dict__)  # noqa: S102

    class _FakeCredential:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class _FakeProjectClient:
        def __init__(self, *, endpoint, credential):
            assert endpoint == "https://foundry.example.com/projects/acme"
            assert isinstance(credential, _FakeCredential)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class _FakeFoundryAgent:
        def __init__(self, **_kwargs):
            pass

        async def run(self, message, *, tools=None, stream=False):
            assert "blind_mqm_n30_package.json" in message
            assert '"documents": []' in message
            assert tools is None
            assert stream is True
            yield types.SimpleNamespace(text="Processed the uploaded representative sample.")

    generated_module.DefaultAzureCredential = _FakeCredential
    generated_module.AIProjectClient = _FakeProjectClient
    generated_module.FoundryAgent = _FakeFoundryAgent
    monkeypatch.setenv("FOUNDRY_ENDPOINT", "https://foundry.example.com/projects/acme")
    monkeypatch.setenv("FOUNDRY_PROJECT_NAME", "acme")

    async def _collect_events() -> list[dict[str, object]]:
        request = generated_module.InvokeRequest(
            message='{"runId":"test-run"}',
            attachments=[
                generated_module.Attachment(
                    name="blind_mqm_n30_package.json", content='{"documents": []}'
                )
            ],
        )
        return [
            json.loads(event.removeprefix("data: ").strip())
            async for event in generated_module._stream_agent_response(request)
        ]

    events = asyncio.run(_collect_events())

    assert events == [
        {"delta": "Processed the uploaded representative sample."},
        {"done": True, "output_text": "Processed the uploaded representative sample."}
    ]


def test_generate_agent_config_module_embeds_the_real_agent_foundry_name_mapping():
    module_source = generate_agent_config_module(
        {"Requirements Specialist": "acme-requirements-specialist", "orchestrator": "acme-orchestrator"}
    )

    assert "AGENT_FOUNDRY_NAMES" in module_source
    assert "'Requirements Specialist': 'acme-requirements-specialist'" in module_source
    assert "'orchestrator': 'acme-orchestrator'" in module_source


_MULTI_PAGE_OUTPUT = '''
```python
# agent: orchestrator
class OrchestratorAgent:
    async def run(self, ui_message: str, on_progress=None) -> None:
        pass
```

```tsx
// agent: page:Catalog Page
export default function CatalogPage() {
    return null;
}
```

```tsx
// agent: page:Dashboard Page
export default function DashboardPage() {
    return null;
}
```
'''


def test_materialize_build_parses_multiple_page_components():
    build = materialize_build(_MULTI_PAGE_OUTPUT)

    assert build.ui_component is None
    assert list(build.page_components.keys()) == ["Catalog Page", "Dashboard Page"]
    assert "CatalogPage" in build.page_components["Catalog Page"]
    assert "DashboardPage" in build.page_components["Dashboard Page"]


def test_materialize_build_rejects_both_single_page_and_multi_page_markers():
    output = _MULTI_PAGE_OUTPUT + '''
```tsx
// agent: ui
export default function MissionApp() {
    return null;
}
```
'''
    with pytest.raises(MaterializedCodeError, match="never both at once"):
        materialize_build(output)


def test_write_to_directory_writes_pages_and_generated_routing_shell(tmp_path: Path):
    build = materialize_build(_MULTI_PAGE_OUTPUT)

    written = build.write_to_directory(tmp_path)

    assert (tmp_path / "pages" / "catalog_page.tsx").exists()
    assert (tmp_path / "pages" / "dashboard_page.tsx").exists()
    assert (tmp_path / "orchestrator.py").exists()
    shell_path = tmp_path / "MissionApp.tsx"
    assert shell_path.exists()
    shell_source = shell_path.read_text(encoding="utf-8")
    assert "agent: generated-routing-shell" in shell_source
    assert 'import Page0 from "./pages/catalog_page";' in shell_source
    assert 'import Page1 from "./pages/dashboard_page";' in shell_source
    assert '"/catalog-page"' in shell_source
    assert '"/dashboard-page"' in shell_source
    assert len(written) == 4


def test_generate_routing_shell_defaults_to_the_first_declared_page():
    shell_source = generate_routing_shell(("Catalog Page", "Dashboard Page"))

    assert 'Navigate to={MISSION_PAGES[0].path}' in shell_source
    assert 'label: "Catalog Page"' in shell_source
    assert 'label: "Dashboard Page"' in shell_source


def test_generate_routing_shell_rejects_empty_page_list():
    with pytest.raises(MaterializedCodeError):
        generate_routing_shell(())


_MULTI_COMPONENT_TYPE_OUTPUT = '''
```python
# agent: orchestrator
class OrchestratorAgent:
    async def run(self, ui_message: str, on_progress=None) -> None:
        pass
```

```python
# agent: service:Entitlement Checker
class EntitlementChecker:
    def check(self) -> bool:
        return True
```

```python
# agent: model:SandboxTenant
from pydantic import BaseModel

class SandboxTenant(BaseModel):
    tenant_id: str
```

```python
# agent: model:Entitlement
from pydantic import BaseModel

class Entitlement(BaseModel):
    product_id: str
```

```yaml
# agent: api_contract:Payments API
openapi: "3.0.0"
info:
  title: Payments API
  version: "1.0"
```
'''


def test_materialize_build_parses_service_model_and_api_contract_components():
    build = materialize_build(_MULTI_COMPONENT_TYPE_OUTPUT)

    assert "EntitlementChecker" in build.service_modules["Entitlement Checker"]
    assert "class SandboxTenant" in build.data_model_modules["SandboxTenant"]
    assert "class Entitlement" in build.data_model_modules["Entitlement"]
    assert "openapi" in build.api_contract_documents["Payments API"]


def test_write_to_directory_writes_services_models_and_api_contracts(tmp_path: Path):
    build = materialize_build(_MULTI_COMPONENT_TYPE_OUTPUT)

    build.write_to_directory(tmp_path)

    assert (tmp_path / "services" / "entitlement_checker.py").exists()
    assert (tmp_path / "models" / "sandboxtenant.py").exists()
    assert (tmp_path / "models" / "entitlement.py").exists()
    assert (tmp_path / "api-contracts" / "payments_api.yaml").exists()
    init_source = (tmp_path / "models" / "__init__.py").read_text(encoding="utf-8")
    assert "from .sandboxtenant import SandboxTenant" in init_source
    assert "from .entitlement import Entitlement" in init_source
    assert '"SandboxTenant"' in init_source
    assert '"Entitlement"' in init_source


def test_generate_models_init_derives_pascal_case_class_names_from_spaced_names():
    init_source = generate_models_init(("Sandbox Tenant", "Entitlement"))

    assert "from .sandbox_tenant import SandboxTenant" in init_source
    assert "from .entitlement import Entitlement" in init_source
    assert '__all__ = ["SandboxTenant", "Entitlement"]' in init_source


def test_generate_models_init_rejects_empty_model_list():
    with pytest.raises(MaterializedCodeError):
        generate_models_init(())


_GATEWAY_POLICY_OUTPUT = '''
```python
# agent: orchestrator
class OrchestratorAgent:
    async def run(self, ui_message: str, on_progress=None) -> None:
        pass
```

```yaml
# agent: gateway_policy:APIM JWT Policy
required_scopes: ["prototype.access"]
path_rules:
  - path_prefix: "/admin"
    required_scopes: ["prototype.admin"]
```
'''


def test_materialize_build_parses_gateway_policy_components():
    build = materialize_build(_GATEWAY_POLICY_OUTPUT)

    assert "required_scopes" in build.gateway_policy_documents["APIM JWT Policy"]


def test_write_to_directory_writes_gateway_policies(tmp_path: Path):
    build = materialize_build(_GATEWAY_POLICY_OUTPUT)

    build.write_to_directory(tmp_path)

    assert (tmp_path / "gateway-policies" / "apim_jwt_policy.yaml").exists()


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
