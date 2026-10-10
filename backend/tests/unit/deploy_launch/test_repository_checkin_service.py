"""Unit tests for RepositoryCheckinService (real vs Null selection) - the
Deploy & Launch ``commit-generated-repository`` step's GitHub MCP commit
collaborator."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.deploy_launch.repository_checkin_service import (
    NullRepositoryCheckinService,
    RepositoryCheckinError,
    RepositoryCheckinService,
    create_repository_checkin_service,
)
from app.repository_connections.github_mcp_client import GitHubMcpError


def _mcp_result(value: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(value)}]}


class _FakeGitHubMcpClient:
    def __init__(self, *, push_result: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._push_result = push_result if push_result is not None else {"commit": {"sha": "a" * 40}}

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, arguments))
        if name == "get_me":
            return _mcp_result({"login": "genie-bot"})
        if name == "create_repository":
            return _mcp_result({"full_name": f"genie-bot/{arguments['name']}"})
        if name == "push_files":
            return _mcp_result(self._push_result)
        raise AssertionError(f"Unexpected tool call: {name}")


class _FailingGitHubMcpClient:
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        del arguments
        if name == "get_me":
            return {"content": [{"type": "text", "text": json.dumps({"login": "genie-bot"})}]}
        raise GitHubMcpError(f"GitHub MCP rejected tool '{name}' with status 403.")


class _RepositoryAlreadyExistsGitHubMcpClient:
    """Simulates retrying a Deploy & Launch run whose repository was already
    created (and possibly already pushed to) by an earlier attempt for the
    same mission - this is the real, observed GitHub MCP error shape."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, arguments))
        if name == "get_me":
            return _mcp_result({"login": "genie-bot"})
        if name == "create_repository":
            raise GitHubMcpError(
                "failed to create repository: Repository creation failed.\n"
                "Repository.name (custom): name already exists on this account"
            )
        if name == "push_files":
            return _mcp_result({"commit": {"sha": "b" * 40}})
        raise AssertionError(f"Unexpected tool call: {name}")


def _write_sample_build(backend_root: Path, frontend_root: Path) -> None:
    backend_root.mkdir(parents=True, exist_ok=True)
    (backend_root / "main.py").write_text("print('backend')\n", encoding="utf-8")
    frontend_root.mkdir(parents=True, exist_ok=True)
    (frontend_root / "App.tsx").write_text("export default function App() {}\n", encoding="utf-8")


async def test_checkin_creates_repository_and_pushes_materialized_files(tmp_path):
    backend_root = tmp_path / "backend"
    frontend_root = tmp_path / "frontend"
    _write_sample_build(backend_root, frontend_root)
    client = _FakeGitHubMcpClient()
    service = RepositoryCheckinService(client=client)  # type: ignore[arg-type]

    result = await service.checkin(
        mission_slug="aci-poc",
        mission_title="ACI PoC",
        backend_root=backend_root,
        frontend_root=frontend_root,
    )

    assert result.repository_url == "https://github.com/genie-bot/genie-proto-aci-poc"
    assert result.commit_sha == "a" * 40
    create_call = next(call for name, call in client.calls if name == "create_repository")
    assert create_call["private"] is True
    assert create_call["autoInit"] is True
    push_call = next(call for name, call in client.calls if name == "push_files")
    assert push_call["owner"] == "genie-bot"
    assert push_call["repo"] == "genie-proto-aci-poc"
    assert push_call["branch"] == "main"
    pushed_paths = {entry["path"] for entry in push_call["files"]}
    assert pushed_paths == {"backend/main.py", "frontend/App.tsx"}


async def test_checkin_fails_closed_when_no_materialized_files_exist(tmp_path):
    service = RepositoryCheckinService(client=_FakeGitHubMcpClient())  # type: ignore[arg-type]

    with pytest.raises(RepositoryCheckinError, match="No materialized"):
        await service.checkin(
            mission_slug="aci-poc",
            mission_title="ACI PoC",
            backend_root=tmp_path / "backend",
            frontend_root=tmp_path / "frontend",
        )


async def test_checkin_fails_closed_when_github_mcp_rejects_the_push(tmp_path):
    backend_root = tmp_path / "backend"
    frontend_root = tmp_path / "frontend"
    _write_sample_build(backend_root, frontend_root)
    service = RepositoryCheckinService(client=_FailingGitHubMcpClient())  # type: ignore[arg-type]

    with pytest.raises(RepositoryCheckinError, match="status 403"):
        await service.checkin(
            mission_slug="aci-poc",
            mission_title="ACI PoC",
            backend_root=backend_root,
            frontend_root=frontend_root,
        )


async def test_checkin_pushes_to_the_existing_repository_when_already_created(tmp_path):
    """Retrying Deploy & Launch after a later step failed must not treat an
    already-created repository (from this exact mission's earlier attempt)
    as a fatal error - it should still push the latest build."""

    backend_root = tmp_path / "backend"
    frontend_root = tmp_path / "frontend"
    _write_sample_build(backend_root, frontend_root)
    client = _RepositoryAlreadyExistsGitHubMcpClient()
    service = RepositoryCheckinService(client=client)  # type: ignore[arg-type]

    result = await service.checkin(
        mission_slug="aci-poc",
        mission_title="ACI PoC",
        backend_root=backend_root,
        frontend_root=frontend_root,
    )

    assert result.repository_url == "https://github.com/genie-bot/genie-proto-aci-poc"
    assert result.commit_sha == "b" * 40
    push_call = next(call for name, call in client.calls if name == "push_files")
    assert push_call["repo"] == "genie-proto-aci-poc"


async def test_checkin_fails_closed_when_github_mcp_omits_a_commit_sha(tmp_path):
    backend_root = tmp_path / "backend"
    frontend_root = tmp_path / "frontend"
    _write_sample_build(backend_root, frontend_root)
    client = _FakeGitHubMcpClient(push_result={})
    service = RepositoryCheckinService(client=client)  # type: ignore[arg-type]

    with pytest.raises(RepositoryCheckinError, match="did not return a commit SHA"):
        await service.checkin(
            mission_slug="aci-poc",
            mission_title="ACI PoC",
            backend_root=backend_root,
            frontend_root=frontend_root,
        )


async def test_null_service_skips_checkin_without_fabricating_a_result(tmp_path):
    service = NullRepositoryCheckinService()

    result = await service.checkin(
        mission_slug="aci-poc",
        mission_title="ACI PoC",
        backend_root=tmp_path / "backend",
        frontend_root=tmp_path / "frontend",
    )

    assert result is None


def test_factory_selects_null_when_github_mcp_client_is_not_configured():
    assert isinstance(
        create_repository_checkin_service(github_mcp_client=None), NullRepositoryCheckinService
    )


def test_factory_selects_real_service_when_github_mcp_client_is_configured():
    client = _FakeGitHubMcpClient()
    service = create_repository_checkin_service(github_mcp_client=client)  # type: ignore[arg-type]
    assert isinstance(service, RepositoryCheckinService)
