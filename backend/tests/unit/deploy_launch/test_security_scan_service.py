"""Unit tests for SecurityScanService (real bandit + TS regex static analysis)."""
from __future__ import annotations

from pathlib import Path

from app.deploy_launch.security_scan_service import SecurityScanService


async def test_scan_flags_a_real_bandit_finding_in_generated_python(tmp_path: Path):
    (tmp_path / "agents").mkdir(parents=True)
    (tmp_path / "agents" / "risky.py").write_text(
        "import subprocess\n\n"
        "def run(user_input: str) -> None:\n"
        "    subprocess.call(user_input, shell=True)\n",
        encoding="utf-8",
    )
    service = SecurityScanService(timeout_seconds=60)

    result = await service.scan(build_root=tmp_path)

    assert result.ran is True
    assert any("risky.py" in f.file for f in result.findings)


async def test_scan_flags_hardcoded_secret_in_tsx(tmp_path: Path):
    (tmp_path / "MissionApp.tsx").write_text(
        "const apiKey = 'sk-abcdefghijklmnop12345';\nexport function App() { return null; }\n",
        encoding="utf-8",
    )
    service = SecurityScanService()

    result = await service.scan(build_root=tmp_path)

    assert any(f.severity == "critical" for f in result.findings)
    assert result.blocking is True


async def test_scan_reports_no_findings_for_clean_code(tmp_path: Path):
    (tmp_path / "clean.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
    service = SecurityScanService()

    result = await service.scan(build_root=tmp_path)

    assert result.findings == []
    assert result.blocking is False


async def test_scan_reports_no_findings_when_build_root_has_no_source_files(tmp_path: Path):
    service = SecurityScanService()

    result = await service.scan(build_root=tmp_path)

    assert result.ran is True
    assert result.findings == []
    assert result.blocking is False


async def test_scan_flags_a_non_tls_url_as_non_blocking(tmp_path: Path):
    """A medium-severity finding (e.g. a plain ``http://`` URL) must be
    reported but must never block deployment on its own - only
    critical/high findings do."""

    (tmp_path / "MissionApp.tsx").write_text(
        "const endpoint = 'http://example.com/api';\nexport function App() { return null; }\n",
        encoding="utf-8",
    )
    service = SecurityScanService()

    result = await service.scan(build_root=tmp_path)

    assert any(f.severity == "medium" for f in result.findings)
    assert result.blocking is False


async def test_scan_flags_eval_usage_in_typescript(tmp_path: Path):
    (tmp_path / "MissionApp.tsx").write_text(
        "export function run(code: string) { return eval(code); }\n", encoding="utf-8"
    )
    service = SecurityScanService()

    result = await service.scan(build_root=tmp_path)

    assert any(f.severity == "high" and "eval(" in f.description for f in result.findings)
    assert result.blocking is True
