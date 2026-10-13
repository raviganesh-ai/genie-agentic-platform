"""Unit tests for DependencyPreflightService (real pip/npm dry-run execution).

No mocking of the service under test - these tests actually run
`pip install --dry-run` and `npm install --dry-run` and assert on the real,
observed result, same discipline as test_test_execution_service.py.
"""

from __future__ import annotations

from pathlib import Path

from app.deploy_launch.dependency_preflight_service import (
    DependencyPreflightService,
)

# Real, observed incident this check exists to catch (ACI PoC mission, this
# session): mcp>=2.0 conflicts with agent-framework-core[all]'s own
# mcp<2,>=1.24.0 pin. agent-framework-core is not actually installed in this
# test environment, so this uses a lighter, self-contained reproduction of
# the same shape of conflict (two already-published packages with
# incompatible version constraints on a shared dependency) rather than
# depending on agent-framework-core being installed here.
_CONFLICTING_REQUIREMENTS = """idna<3.0
requests>=2.32
"""

_VALID_REQUIREMENTS = """idna>=3.0,<4.0
"""


async def test_check_python_requirements_reports_not_checked_when_file_is_missing(
    tmp_path: Path,
):
    service = DependencyPreflightService(timeout_seconds=60)

    result = await service.check_python_requirements(tmp_path / "does-not-exist.txt")

    assert result.checked is False
    assert result.success is False
    assert "does not exist" in result.summary


async def test_check_python_requirements_passes_for_a_real_resolvable_set(tmp_path: Path):
    requirements_path = tmp_path / "requirements.txt"
    requirements_path.write_text(_VALID_REQUIREMENTS, encoding="utf-8")
    service = DependencyPreflightService(timeout_seconds=60)

    result = await service.check_python_requirements(requirements_path)

    assert result.checked is True
    assert result.ok is True
    assert result.success is True
    assert "resolve cleanly" in result.summary


async def test_check_python_requirements_reports_a_real_conflict_not_fabricated_success(
    tmp_path: Path,
):
    requirements_path = tmp_path / "requirements.txt"
    requirements_path.write_text(_CONFLICTING_REQUIREMENTS, encoding="utf-8")
    service = DependencyPreflightService(timeout_seconds=60)

    result = await service.check_python_requirements(requirements_path)

    assert result.checked is True
    assert result.ok is False
    assert result.success is False
    assert "pip dependency resolution failed" in result.summary


async def test_check_npm_requirements_reports_not_checked_when_file_is_missing(tmp_path: Path):
    service = DependencyPreflightService(timeout_seconds=60)

    result = await service.check_npm_requirements(tmp_path / "package.json")

    assert result.checked is False
    assert result.success is False


async def test_check_npm_requirements_passes_for_a_real_resolvable_package_json(
    tmp_path: Path,
):
    (tmp_path / "package.json").write_text(
        '{"name": "fixture", "private": true, "dependencies": {"react": "18.3.1"}}',
        encoding="utf-8",
    )
    service = DependencyPreflightService(timeout_seconds=120)

    result = await service.check_npm_requirements(tmp_path / "package.json")

    assert result.checked is True
    assert result.ok is True, result.raw_output[-2000:]
    assert result.success is True


async def test_check_npm_requirements_reports_a_real_conflict_not_fabricated_success(
    tmp_path: Path,
):
    # react-dom 18.x genuinely requires a matching react peer version - 17.x
    # here is a real, observed-by-npm peer dependency conflict.
    (tmp_path / "package.json").write_text(
        '{"name": "fixture", "private": true, '
        '"dependencies": {"react": "17.0.2", "react-dom": "18.3.1"}}',
        encoding="utf-8",
    )
    service = DependencyPreflightService(timeout_seconds=120)

    result = await service.check_npm_requirements(tmp_path / "package.json")

    assert result.checked is True
    assert result.ok is False, result.raw_output[-2000:]
    assert result.success is False
    assert "npm dependency resolution failed" in result.summary
