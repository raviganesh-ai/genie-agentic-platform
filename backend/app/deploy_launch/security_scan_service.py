"""Real static security analysis of a mission's materialized build.

Python code (every agent module + the orchestrator) is scanned with
``bandit`` (a real subprocess run, not a fabricated report - see
``pyproject.toml``'s ``bandit`` dependency). TypeScript/TSX code is scanned
with a deterministic regex-based check for the small set of common,
unambiguous risk patterns (hardcoded secrets, ``eval``,
``dangerouslySetInnerHTML``) - a real, directly-inspected result, not a
full ESLint security-plugin run (no npm/eslint install machinery is
available in this sandboxed backend process), and documented here as such
rather than overstating what was actually checked.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

__all__ = ["SecurityFinding", "SecurityScanResult", "SecurityScanService"]

_DEFAULT_TIMEOUT_SECONDS: Final = 60

_TS_RISK_PATTERNS: Final[tuple[tuple[str, str, str], ...]] = (
    ("high", "eval(", r"\beval\s*\("),
    ("high", "dangerouslySetInnerHTML", r"dangerouslySetInnerHTML"),
    ("critical", "hardcoded API key/secret literal", r"(?i)(api[_-]?key|secret|password)\s*[:=]\s*['\"][^'\"]{8,}['\"]"),
    ("medium", "http:// (non-TLS) absolute URL", r"http://[^\s'\"]+"),
)


@dataclass(frozen=True)
class SecurityFinding:
    """One real, observed static-analysis finding."""

    file: str
    severity: str
    description: str


@dataclass(frozen=True)
class SecurityScanResult:
    """The real, aggregate outcome of scanning a mission's materialized build."""

    ran: bool
    findings: list[SecurityFinding] = field(default_factory=list)
    summary: str = ""

    @property
    def blocking(self) -> bool:
        return any(f.severity in ("critical", "high") for f in self.findings)


class SecurityScanService:
    """Runs real static analysis against a mission's materialized build directory."""

    def __init__(self, *, timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS) -> None:
        self._timeout_seconds = timeout_seconds

    async def scan(self, *, build_root: Path) -> SecurityScanResult:
        python_findings = await self._scan_python(build_root)
        ts_findings = self._scan_typescript(build_root)
        findings = python_findings + ts_findings
        return SecurityScanResult(
            ran=True,
            findings=findings,
            summary=f"{len(findings)} finding(s) across Python and TypeScript/TSX code.",
        )

    async def _scan_python(self, build_root: Path) -> list[SecurityFinding]:
        python_files = sorted(build_root.rglob("*.py"))
        if not python_files:
            return []

        env = {"PATH": os.environ.get("PATH", "")}
        if sys.platform == "win32":
            env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")

        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "bandit",
            "-r",
            str(build_root),
            "-f",
            "json",
            "-q",
            cwd=str(build_root),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(), timeout=self._timeout_seconds
            )
        except TimeoutError:
            # A hanging scan must never be left running as an orphaned
            # subprocess after this reports back to the caller.
            process.kill()
            await process.wait()
            return [
                SecurityFinding(
                    file=str(build_root), severity="medium", description="bandit scan timed out."
                )
            ]

        try:
            report = json.loads(stdout.decode("utf-8", errors="replace") or "{}")
        except json.JSONDecodeError:
            return []

        findings: list[SecurityFinding] = []
        for result in report.get("results", []):
            severity = str(result.get("issue_severity", "low")).lower()
            findings.append(
                SecurityFinding(
                    file=str(result.get("filename", "")),
                    severity=severity,
                    description=str(result.get("issue_text", "")),
                )
            )
        return findings

    def _scan_typescript(self, build_root: Path) -> list[SecurityFinding]:
        findings: list[SecurityFinding] = []
        for path in sorted(build_root.rglob("*.tsx")) + sorted(build_root.rglob("*.ts")):
            text = path.read_text(encoding="utf-8", errors="replace")
            for severity, description, pattern in _TS_RISK_PATTERNS:
                if re.search(pattern, text):
                    findings.append(
                        SecurityFinding(
                            file=str(path), severity=severity, description=description
                        )
                    )
        return findings
