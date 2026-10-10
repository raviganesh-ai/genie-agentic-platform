"""Commits one mission's materialized generated prototype build to a
dedicated GitHub repository via the already-configured GitHub MCP
connection.

Mirrors ``app.modernization.service``'s ``push_files``/``create_pull_request``
usage - the same real, already-proven GitHub MCP tool-calling pattern - but
creates a brand-new repository per mission instead of opening a pull request
against an existing one, since a generated prototype has no pre-existing
repository to target.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.deploy_launch.resource_naming import prototype_repository_name
from app.repository_connections.github_mcp_client import GitHubMcpClient, GitHubMcpError

__all__ = [
    "NullRepositoryCheckinService",
    "RepositoryCheckinError",
    "RepositoryCheckinResult",
    "RepositoryCheckinService",
    "create_repository_checkin_service",
]


class RepositoryCheckinError(RuntimeError):
    """Raised when the generated prototype cannot be committed to GitHub."""


@dataclass(frozen=True)
class RepositoryCheckinResult:
    repository_url: str
    commit_sha: str


def _collect_files(*roots: tuple[str, Path]) -> list[dict[str, str]]:
    """Reads every real file under each ``(prefix, root)`` pair into a
    ``push_files`` entry - skips directories and any file that is not
    valid UTF-8 text (the materialized build is entirely source/config
    text; a non-text file here would indicate an unexpected artifact that
    must never be silently corrupted by a text-mode push)."""

    files: list[dict[str, str]] = []
    for prefix, root in roots:
        if not root.exists():
            continue
        for file_path in sorted(root.rglob("*")):
            if not file_path.is_file():
                continue
            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            arcname = f"{prefix}/{file_path.relative_to(root).as_posix()}"
            files.append({"path": arcname, "content": content})
    return files


def _find_commit_sha(push_result: Any) -> str | None:
    """Extracts the real commit SHA from the GitHub MCP ``push_files``
    result - never fabricated. ``push_files`` responses observed from the
    GitHub MCP server carry the new commit under a top-level ``commit``
    object; tolerate a bare top-level ``sha`` too rather than assume one
    exact shape, but never invent a value when neither is present."""

    if not isinstance(push_result, dict):
        return None
    commit = push_result.get("commit")
    if isinstance(commit, dict):
        sha = commit.get("sha")
        if isinstance(sha, str) and sha:
            return sha
    sha = push_result.get("sha")
    if isinstance(sha, str) and sha:
        return sha
    return None


class RepositoryCheckinService:
    """Real path: creates one dedicated, private GitHub repository per
    mission and pushes its materialized backend + frontend build as a
    single commit - the real, deterministic output of a successful
    deployment, never a fabricated placeholder."""

    def __init__(self, *, client: GitHubMcpClient) -> None:
        self._client = client

    async def checkin(
        self,
        *,
        mission_slug: str,
        mission_title: str,
        backend_root: Path,
        frontend_root: Path,
    ) -> RepositoryCheckinResult:
        files = _collect_files(("backend", backend_root), ("frontend", frontend_root))
        if not files:
            raise RepositoryCheckinError(
                "No materialized backend/frontend files were found to commit."
            )
        repo_name = prototype_repository_name(mission_slug)
        try:
            account = GitHubMcpClient.tool_json(await self._client.call_tool("get_me", {}))
            owner = account.get("login") if isinstance(account, dict) else None
            if not owner:
                raise RepositoryCheckinError(
                    "GitHub MCP did not return an authenticated account login."
                )
            try:
                await self._client.call_tool(
                    "create_repository",
                    {
                        "name": repo_name,
                        "description": f"Genie-generated prototype for mission '{mission_title}'.",
                        "private": True,
                        "autoInit": True,
                    },
                )
            except GitHubMcpError as exc:
                # Idempotent retry: a prior attempt for this exact mission
                # (same deterministic repo_name, see prototype_repository_name)
                # may have already created the repository and pushed the
                # build before a LATER pipeline step failed and the whole
                # run was retried. Re-raising here would make every retry
                # after that point permanently unrecoverable - GitHub never
                # allows re-creating a repo with the same name - even though
                # this mission's repository already exists and only needs
                # its latest build pushed. Only this specific, recognized
                # "already exists" condition is treated as non-fatal; any
                # other repository-creation failure still fails the step.
                if "name already exists on this account" not in str(exc):
                    raise
            push_result = GitHubMcpClient.tool_json(
                await self._client.call_tool(
                    "push_files",
                    {
                        "owner": owner,
                        "repo": repo_name,
                        "branch": "main",
                        "message": f"Genie-generated prototype build for '{mission_title}'",
                        "files": files,
                    },
                )
            )
        except GitHubMcpError as exc:
            raise RepositoryCheckinError(str(exc)) from exc
        commit_sha = _find_commit_sha(push_result)
        if not commit_sha:
            raise RepositoryCheckinError(
                "GitHub MCP did not return a commit SHA for the pushed generated prototype."
            )
        return RepositoryCheckinResult(
            repository_url=f"https://github.com/{owner}/{repo_name}",
            commit_sha=commit_sha,
        )


class NullRepositoryCheckinService:
    """Local/test double: GitHub MCP is not configured - the
    ``commit-generated-repository`` step is skipped rather than fabricating
    a repository URL/commit SHA that was never actually created."""

    async def checkin(
        self,
        *,
        mission_slug: str,
        mission_title: str,
        backend_root: Path,
        frontend_root: Path,
    ) -> RepositoryCheckinResult | None:
        del mission_slug, mission_title, backend_root, frontend_root
        return None


def create_repository_checkin_service(
    *, github_mcp_client: GitHubMcpClient | None
) -> RepositoryCheckinService | NullRepositoryCheckinService:
    """Builds the real service when GitHub MCP is configured, otherwise the
    Null double - mirrors every other optional-collaborator factory in this
    package (e.g. ``create_backend_deployment_service``'s own
    prototype-gateway fallback)."""

    if github_mcp_client is None:
        return NullRepositoryCheckinService()
    return RepositoryCheckinService(client=github_mcp_client)
