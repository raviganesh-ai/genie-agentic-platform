"""Unit tests for TestExecutionService (real sandboxed pytest execution)."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from app.deploy_launch.test_execution_service import (
    TestExecutionService,
    _AuthenticatedTestProxy,
    extract_test_modules,
    has_pytest_discoverable_tests,
    validate_real_action_tests,
)

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

_COLLECTION_ERROR_TEST = """
```python
import a_package_that_is_not_installed


def test_req_001_uses_an_uninstalled_package():
    assert True
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


def test_detects_pytest_discoverable_function_and_rejects_helper_only_module():
    assert has_pytest_discoverable_tests(extract_test_modules(_PASSING_TEST))
    assert not has_pytest_discoverable_tests(extract_test_modules(_NO_TEST_FUNCTIONS))


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
    assert result.passed_test_names == ("test_always_passes",)
    assert result.failed_test_names == ()
    assert (tmp_path / "tests" / "test_generated_0.py").exists()


async def test_run_tests_reports_real_failures_not_fabricated_success(tmp_path: Path):
    service = TestExecutionService(timeout_seconds=60)

    result = await service.run_tests(build_root=tmp_path, test_output_text=_FAILING_TEST)

    assert result.ran is True
    assert result.failed == 1
    assert result.success is False
    assert result.failed_test_names == ("test_always_fails",)


async def test_run_tests_treats_mixed_pass_and_fail_modules_correctly(tmp_path: Path):
    service = TestExecutionService(timeout_seconds=60)

    result = await service.run_tests(
        build_root=tmp_path, test_output_text=_PASSING_TEST + _FAILING_TEST
    )

    assert result.passed == 1
    assert result.failed == 1
    assert result.success is False
    assert result.passed_test_names == ("test_always_passes",)
    assert result.failed_test_names == ("test_always_fails",)


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
    assert result.timed_out is True
    assert "timed out" in result.summary


def test_summarize_reports_pytest_never_launching_distinctly_from_zero_collected(tmp_path: Path):
    """A production runtime missing the ``pytest`` package (e.g. `pip
    install .` without the `dev` extra) makes `python -m pytest` fail
    before pytest ever prints its own banner - e.g. `No module named
    pytest`. That must be reported as a real execution failure, never
    misread as "the generated test suite has no runnable test
    function(s)", which wrongly blames the generated code instead of the
    environment."""

    service = TestExecutionService()

    result = service._summarize(
        raw_output="/usr/local/bin/python3.12: No module named pytest\n",
        exit_code=1,
    )

    assert result.ran is False
    assert result.success is False
    assert "pytest did not run" in result.summary
    assert "No tests were collected" not in result.summary


def test_summarize_still_reports_zero_collected_when_pytest_genuinely_ran(tmp_path: Path):
    raw_output = (
        "============================= test session starts ==============================\n"
        "collected 0 items\n"
        "============================== no tests ran in 0.01s ==============================\n"
    )

    service = TestExecutionService()

    result = service._summarize(raw_output=raw_output, exit_code=5)

    assert result.ran is True
    assert result.success is False
    assert "No tests were collected" in result.summary


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


async def test_run_tests_one_modules_collection_error_does_not_blank_out_the_others(
    tmp_path: Path,
):
    """Regression test: a generated test module that fails to import (e.g.
    it uses a package that isn't installed in this pipeline's execution
    environment) must not zero out every OTHER module's real results -
    pytest's default behavior is to run NO tests at all when ANY module has
    a collection error, which used to misreport every requirement as
    having no observed JUnit result whatsoever, not just the broken one."""

    service = TestExecutionService(timeout_seconds=60)

    result = await service.run_tests(
        build_root=tmp_path, test_output_text=_COLLECTION_ERROR_TEST + _PASSING_TEST
    )

    assert result.ran is True
    assert result.passed_test_names == ("test_always_passes",)
    # pytest reports a module collection failure as ONE synthetic testcase
    # named after the FILE (e.g. "test_generated_0"), never the real
    # `test_req_001_uses_an_uninstalled_package` function inside it - without
    # mapping it back, the Requirement Fidelity Gate misreports this as "no
    # JUnit result" (looks like the test never ran) instead of the real,
    # actionable "errored" outcome.
    assert result.errored_test_names == ("test_req_001_uses_an_uninstalled_package",)


def test_real_action_policy_rejects_mocks_and_tests_without_deployed_urls() -> None:
    assert validate_real_action_tests(
        [("from unittest.mock import patch\ndef test_req_001():\n    assert patch('app.run')")]
    ) == (
        "Acceptance tests contain a mock, patch, or interception library.",
        "Acceptance tests do not reference MISSION_BACKEND_URL or MISSION_FRONTEND_URL.",
    )


def test_real_action_policy_accepts_black_box_test_using_deployed_url() -> None:
    reasons = validate_real_action_tests(
        [
            (
                "import os\nimport httpx\n"
                "def test_req_001_live_health():\n"
                "    response = httpx.get(os.environ['MISSION_BACKEND_URL'] + '/health')\n"
                "    assert response.status_code == 200"
            )
        ]
    )

    assert reasons == ()


async def test_run_tests_keeps_injected_access_token_out_of_generated_process(tmp_path: Path):
    access_token = "eyJ-sensitive-prototype-token"
    output = '''\n```python
import os

def test_token_is_not_available_to_generated_code():
    assert "MISSION_ACCESS_TOKEN" not in os.environ
    assert os.environ["MISSION_BACKEND_URL"].startswith("http://127.0.0.1:")
    assert os.environ["MISSION_UNAUTHENTICATED_BACKEND_URL"] == "https://prototype.example.com"
```\n'''
    service = TestExecutionService(timeout_seconds=60)

    result = await service.run_tests(
        build_root=tmp_path,
        test_output_text=output,
        runtime_environment={
            "MISSION_ACCESS_TOKEN": access_token,
            "MISSION_BACKEND_URL": "https://prototype.example.com",
        },
    )

    assert result.success
    assert access_token not in result.raw_output
    assert access_token not in result.summary


def test_authenticated_test_proxy_injects_token_only_at_fixed_upstream() -> None:
    observed: dict[str, str] = {}

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            observed["authorization"] = self.headers.get("Authorization", "")
            observed["path"] = self.path
            self.send_response(201)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    host, port = upstream.server_address
    proxy = _AuthenticatedTestProxy(
        backend_url=f"http://{host}:{port}", access_token="prototype-token"
    )
    proxy.start()
    try:
        response = httpx.post(proxy.url + "/invoke?case=1", content=b"{}")
    finally:
        proxy.close()
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=5)

    assert response.status_code == 201
    assert response.text == "ok"
    assert observed == {
        "authorization": "Bearer prototype-token",
        "path": "/invoke?case=1",
    }
