"""Unit tests for TestExecutionService (real sandboxed pytest execution)."""
from __future__ import annotations

from pathlib import Path

import pytest

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

_NO_TEST_FUNCTIONS = """
```python
def helper_not_a_test():
    return 1 + 1
```
"""

_SYNTAX_ERROR_TEST = """
```python
def test_broken(:
    assert True
```
"""

_SLOW_TEST = """
```python
import time


def test_too_slow():
    time.sleep(5)
    assert True
```
"""

_ENV_ISOLATION_TEST = """
```python
import os


def test_no_ambient_secret_is_visible():
    assert os.environ.get("GENIE_TEST_SECRET_LEAK") is None
```
"""


def test_extract_test_modules_returns_each_fenced_block():
    modules = extract_test_modules(_PASSING_TEST + _FAILING_TEST)

    assert len(modules) == 2
    assert "test_always_passes" in modules[0]
    assert "test_always_fails" in modules[1]


def test_extract_test_modules_returns_empty_list_for_narrative_only_text():
    assert extract_test_modules("Here is a narrative description with no code.") == []


def test_extract_test_modules_is_case_insensitive_on_the_fence_language():
    modules = extract_test_modules("```PYTHON\ndef test_x():\n    assert True\n```")

    assert len(modules) == 1
    assert "test_x" in modules[0]


async def test_run_tests_reports_no_tests_found_when_output_has_no_code(tmp_path: Path):
    service = TestExecutionService()

    result = await service.run_tests(build_root=tmp_path, test_output_text="just narrative text")

    assert result.ran is False
    assert result.success is False
    assert "No test code" in result.summary


async def test_run_tests_actually_executes_generated_pytest_module(tmp_path: Path):
    service = TestExecutionService(timeout_seconds=60)

    result = await service.run_tests(build_root=tmp_path, test_output_text=_PASSING_TEST)

    assert result.ran is True
    assert result.passed == 1
    assert result.failed == 0
    assert result.success is True
    assert (tmp_path / "tests" / "test_generated_0.py").exists()


async def test_run_tests_reports_real_failures_not_fabricated_success(tmp_path: Path):
    service = TestExecutionService(timeout_seconds=60)

    result = await service.run_tests(build_root=tmp_path, test_output_text=_FAILING_TEST)

    assert result.ran is True
    assert result.failed == 1
    assert result.success is False


async def test_run_tests_treats_mixed_pass_and_fail_modules_correctly(tmp_path: Path):
    service = TestExecutionService(timeout_seconds=60)

    result = await service.run_tests(
        build_root=tmp_path, test_output_text=_PASSING_TEST + _FAILING_TEST
    )

    assert result.passed == 1
    assert result.failed == 1
    assert result.success is False


async def test_run_tests_does_not_treat_zero_collected_tests_as_success(tmp_path: Path):
    """A generated module with no ``test_``-prefixed function collects zero
    tests - pytest itself exits 0 for this, which must never read as
    "the generated tests passed": nothing was actually verified."""

    service = TestExecutionService(timeout_seconds=60)

    result = await service.run_tests(build_root=tmp_path, test_output_text=_NO_TEST_FUNCTIONS)

    assert result.ran is True
    assert result.passed == 0
    assert result.failed == 0
    assert result.errors == 0
    assert result.success is False
    assert "No tests were collected" in result.summary


async def test_run_tests_reports_a_generated_syntax_error_as_a_real_failure(tmp_path: Path):
    service = TestExecutionService(timeout_seconds=60)

    result = await service.run_tests(build_root=tmp_path, test_output_text=_SYNTAX_ERROR_TEST)

    assert result.ran is True
    assert result.success is False
    assert result.errors >= 1 or result.exit_code != 0


async def test_run_tests_times_out_on_a_hanging_generated_test(tmp_path: Path):
    service = TestExecutionService(timeout_seconds=1)

    result = await service.run_tests(build_root=tmp_path, test_output_text=_SLOW_TEST)

    assert result.ran is True
    assert result.success is False
    assert "timed out" in result.summary


async def test_run_tests_subprocess_does_not_leak_ambient_environment_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Proves the sandboxed subprocess really only gets ``PATH``/``SYSTEMROOT``
    - not a mocked assertion about the env dict, but a generated test that
    itself checks its own process environment and is then really executed."""

    monkeypatch.setenv("GENIE_TEST_SECRET_LEAK", "leaked-value")
    service = TestExecutionService(timeout_seconds=60)

    result = await service.run_tests(build_root=tmp_path, test_output_text=_ENV_ISOLATION_TEST)

    assert result.success is True
    assert result.passed == 1

