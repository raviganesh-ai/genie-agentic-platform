"""Architecture boundary test: azure-ai-projects/azure-identity isolation.

Per the Phase 3 course-correction ("Production Agent Rules" in
``.github/copilot-instructions.md``): only a small, explicit set of access
layer modules may import the azure-ai-projects / azure-identity SDKs
directly, each isolating that SDK behind its own interface:

- ``app/agents/foundry/project_service.py`` owns the AIProjectClient /
  DefaultAzureCredential lifecycle for agent execution
  (``AgentApiClient`` / ``FoundryAgentClient`` protocols).
- ``app/agents/foundry/api_client.py`` constructs
  ``azure.ai.projects.models`` request objects (e.g.
  ``PromptAgentDefinition``) for the versioned Foundry Agents admin API
  (``create_version``/``get``/``delete``) - it never constructs a
  credential or SDK client itself (that remains ``project_service.py``'s
  job), but it does need the SDK's request-model types.
- ``app/deployment/provider_status_source.py`` owns the
  ResourceManagementClient / DefaultAzureCredential lifecycle for
  pre-provisioning Azure subscription readiness checks
  (``ResourceProviderStatusSource`` protocol).
- ``app/transcription/speech_service.py`` owns the ``DefaultAzureCredential``
  lifecycle for Azure AI Speech call-transcript transcription
  (``SpeechToTextService`` protocol).

Every other module must depend only on those protocols, never on the SDK
directly. This test fails closed if that boundary is ever violated.
"""
from __future__ import annotations

import re
from pathlib import Path

_SDK_IMPORT_PATTERN = re.compile(r"^\s*(?:import|from)\s+azure\.(ai\.projects|identity)\b", re.MULTILINE)

# Only these files are allowed to import the SDK directly - each owns one
# SDK client's lifecycle behind its own protocol. Everything else must
# depend on those protocols instead.
_ALLOWED_RELATIVE_PATHS = {
    Path("agents/foundry/project_service.py"),
    Path("agents/foundry/api_client.py"),
    Path("deployment/provider_status_source.py"),
    Path("transcription/speech_service.py"),
}


def _app_root() -> Path:
    # backend/tests/unit/test_architecture_boundary.py -> backend/app
    return Path(__file__).resolve().parents[2] / "app"


def test_azure_sdk_imports_are_confined_to_the_foundry_access_layer():
    app_root = _app_root()
    violations: list[str] = []

    for py_file in app_root.rglob("*.py"):
        relative = py_file.relative_to(app_root)
        if relative in _ALLOWED_RELATIVE_PATHS:
            continue
        text = py_file.read_text(encoding="utf-8")
        if _SDK_IMPORT_PATTERN.search(text):
            violations.append(str(relative))

    assert violations == [], (
        "azure-ai-projects/azure-identity must only be imported from one of "
        f"{sorted(str(p) for p in _ALLOWED_RELATIVE_PATHS)}, but found imports in: {violations}"
    )


def test_foundry_access_layer_actually_imports_the_sdk():
    # Sanity check: guards against the allow-list above silently becoming
    # dead weight if project_service.py stops importing the SDK.
    project_service = _app_root() / "agents" / "foundry" / "project_service.py"
    text = project_service.read_text(encoding="utf-8")
    assert _SDK_IMPORT_PATTERN.search(text)


def test_deployment_access_layer_actually_imports_the_sdk():
    # Sanity check: guards against the allow-list above silently becoming
    # dead weight if provider_status_source.py stops importing the SDK.
    provider_status_source = _app_root() / "deployment" / "provider_status_source.py"
    text = provider_status_source.read_text(encoding="utf-8")
    assert _SDK_IMPORT_PATTERN.search(text)


def test_transcription_access_layer_actually_imports_the_sdk():
    # Sanity check: guards against the allow-list above silently becoming
    # dead weight if speech_service.py stops importing the SDK.
    speech_service = _app_root() / "transcription" / "speech_service.py"
    text = speech_service.read_text(encoding="utf-8")
    assert _SDK_IMPORT_PATTERN.search(text)
