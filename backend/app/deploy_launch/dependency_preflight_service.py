"""Real pre-flight dependency-resolution checks before a slow remote build.

Phase B of the Genie platform-change roadmap: a real, observed incident
during the ACI PoC mission this session - a mission's requirements.txt
pinned ``mcp>=2.0,<3.0``, but ``agent-framework-core[all]`` (a transitive
dependency of ``agent-framework``, already required by every generated
mission backend - see ``code_materializer._REQUIREMENTS_TXT``) depends on
``mcp<2``. This was only discovered after a full remote ACR build spent
several minutes uploading, building, and running `pip install` inside the
build container before failing. ``pip install --dry-run`` reproduces the
exact same ``ResolutionImpossible`` error in seconds, run locally, before
any of that slow remote work begins - so a real conflict like this now
fails the pipeline immediately with an actionable pip error message
instead of failing deep inside a remote build log a human has to go find.

Mirrors ``test_execution_service``'s own discipline: a real subprocess, a
bounded timeout, and no fabricated "resolves cleanly" result - absence of
output or a timeout is reported as "could not verify", never silently
treated as success.
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

__all__ = [
    "DependencyPreflightResult",
    "DependencyPreflightService",
]

_DEFAULT_TIMEOUT_SECONDS: Final = 120
_NPM_COMMAND: Final = "npm.cmd" if sys.platform == "win32" else "npm"


@dataclass(frozen=True)
class DependencyPreflightResult:
    """The real, observed outcome of one dependency-resolution dry run."""

    __test__ = False

    checked: bool
    ok: bool = False
    summary: str = ""
    raw_output: str = field(default="", repr=False)
    timed_out: bool = False

    @property
    def success(self) -> bool:
        """Fails closed: a check that never ran (missing file, timed out,
        or the tool itself never launched) is never treated as a pass -
        only a real, observed clean resolution counts."""

        return self.checked and self.ok and not self.timed_out


class DependencyPreflightService:
    """Runs real, local dependency-resolution dry runs for one mission's
    generated backend (``requirements.txt``) and frontend (``package.json``)
    before either is packaged for a real, slow remote build."""

    def __init__(self, *, timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS) -> None:
        self._timeout_seconds = timeout_seconds

    @property
    def timeout_seconds(self) -> int:
        return self._timeout_seconds

    async def check_python_requirements(
        self, requirements_path: Path
    ) -> DependencyPreflightResult:
        """Runs `pip install --dry-run -r requirements.txt` against the
        CURRENT interpreter's already-installed package set - this is a
        lighter-weight check than building a fresh isolated venv per
        mission, deliberately trading "perfectly isolated" for "fast
        enough to run before every deploy, catches the exact class of
        conflict this is meant to catch" (a real version conflict between
        the mission's own pinned packages and an already-required
        transitive dependency, like mcp vs agent-framework-core)."""

        if not requirements_path.exists():
            return DependencyPreflightResult(
                checked=False, summary=f"{requirements_path} does not exist."
            )

        env = {"PATH": os.environ.get("PATH", "")}
        if sys.platform == "win32":
            env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "pip",
            "install",
            "--dry-run",
            "-r",
            str(requirements_path),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(), timeout=self._timeout_seconds
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            return DependencyPreflightResult(
                checked=True,
                timed_out=True,
                summary=f"pip dependency resolution timed out after {self._timeout_seconds}s.",
            )
        raw_output = stdout.decode("utf-8", errors="replace")
        ok = process.returncode == 0
        summary = (
            "Python dependencies resolve cleanly."
            if ok
            else f"pip dependency resolution failed: {raw_output.strip()[-1000:]}"
        )
        return DependencyPreflightResult(checked=True, ok=ok, summary=summary, raw_output=raw_output)

    async def check_npm_requirements(self, package_json_path: Path) -> DependencyPreflightResult:
        """Runs `npm install --dry-run` in the directory containing
        ``package_json_path`` - same rationale as the Python check above."""

        if not package_json_path.exists():
            return DependencyPreflightResult(
                checked=False, summary=f"{package_json_path} does not exist."
            )

        env = {"PATH": os.environ.get("PATH", "")}
        if sys.platform == "win32":
            env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
            for name in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "TEMP", "TMP"):
                value = os.environ.get(name)
                if value:
                    env[name] = value
        process = await asyncio.create_subprocess_exec(
            _NPM_COMMAND,
            "install",
            "--dry-run",
            cwd=str(package_json_path.parent),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(), timeout=self._timeout_seconds
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            return DependencyPreflightResult(
                checked=True,
                timed_out=True,
                summary=f"npm dependency resolution timed out after {self._timeout_seconds}s.",
            )
        raw_output = stdout.decode("utf-8", errors="replace")
        ok = process.returncode == 0
        summary = (
            "npm dependencies resolve cleanly."
            if ok
            else f"npm dependency resolution failed: {raw_output.strip()[-1000:]}"
        )
        return DependencyPreflightResult(checked=True, ok=ok, summary=summary, raw_output=raw_output)
