"""Unit tests for TestExecutionService (real sandboxed pytest execution)."""
from __future__ import annotations

from pathlib import Path

from app.deploy_launch.test_execution_service import TestExecutionService, extract_test_modules

_PASSING_TEST = """
```python
def test_always_passes():
    assert 1 + 1 == 2
```
"""

_FAILING_TEST = """
```python
def test_always_fails():
    assert 1 == 2
```
"""


def test_extract_test_modules_returns_each_fenced_block():
    modules = extract_test_modules(_PASSING_TEST + _FAILING_TEST)

    assert len(modules) == 2
    assert "test_always_passes" in modules[0]
    assert "test_always_fails" in modules[1]


async def test_run_tests_reports_no_tests_found_when_output_has_no_code(tmp_path: Path):
    service = TestExecutionService()

    result = await service.run_tests(build_root=tmp_path, test_output_text="just narrative text")

    assert result.ran is False
    assert "No test code" in result.summary


async def test_run_tests_actually_executes_generated_pytest_module(tmp_path: Path):
    service = TestExecutionService(timeout_seconds=60)

    result = await service.run_tests(build_root=tmp_path, test_output_text=_PASSING_TEST)

    assert result.ran is True
    assert result.passed == 1
    assert result.failed == 0
    assert (tmp_path / "tests" / "test_generated_0.py").exists()


async def test_run_tests_reports_real_failures_not_fabricated_success(tmp_path: Path):
    service = TestExecutionService(timeout_seconds=60)

    result = await service.run_tests(build_root=tmp_path, test_output_text=_FAILING_TEST)

    assert result.ran is True
    assert result.failed == 1
