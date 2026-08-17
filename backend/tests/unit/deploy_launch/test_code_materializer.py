"""Unit tests for materialize_build (code_materializer)."""
from __future__ import annotations

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
async def run() -> None:
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
    assert "async def run" in build.orchestrator_module
    assert build.ui_component is not None
    assert "MissionApp" in build.ui_component


def test_materialize_build_raises_when_no_code_blocks_found():
    with pytest.raises(MaterializedCodeError):
        materialize_build("no code here at all")


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


def test_generate_agent_config_module_embeds_the_real_agent_foundry_name_mapping():
    module_source = generate_agent_config_module(
        {"Requirements Specialist": "acme-requirements-specialist", "orchestrator": "acme-orchestrator"}
    )

    assert "AGENT_FOUNDRY_NAMES" in module_source
    assert "'Requirements Specialist': 'acme-requirements-specialist'" in module_source
    assert "'orchestrator': 'acme-orchestrator'" in module_source
