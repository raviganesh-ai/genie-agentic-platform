"""Real, sandboxed execution of the Test Generation Agent's own generated tests.

Extracts every fenced ``python`` code block from the ``generate-test-suite``
step's own ``output_text`` (the Test Generation Agent's real test code - see
``test-generation-v1`` in ``config/prompts/registry.yaml``), writes each as
a real file alongside the materialized build, and actually executes them
with ``pytest`` in a subprocess - a real, observed pass/fail outcome, never
a fabricated "tests passed" result. A generated test suite that fails to
import or run against its own generated build is reported as a real
failure, not silently treated as success.

The subprocess runs with a stripped environment (only ``PATH``, on Windows
``SYSTEMROOT``, and explicitly allow-listed mission URLs), a bounded timeout,
and its own dedicated working directory. It is process isolation, not an OS
sandbox: generated acceptance tests need network access.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

__all__ = ["TestExecutionResult", "TestExecutionService", "extract_test_modules"]

# Line-anchored so triple backticks inside a generated test's own string
# literals cannot terminate the block early (see code_materializer).
_FENCE_PATTERN: Final = re.compile(
    r"^[ \t]*```python[ \t]*\n(.*?)^[ \t]*```[ \t]*$",
    re.DOTALL | re.IGNORECASE | re.MULTILINE,
)
_PASSED_PATTERN: Final = re.compile(r"(\d+) passed")
_FAILED_PATTERN: Final = re.compile(r"(\d+) failed")
_ERRORS_PATTERN: Final = re.compile(r"(\d+) error(?:s)?")
_PYTEST_FUNCTION_PATTERN: Final = re.compile(r"^\s*def\s+test_[A-Za-z0-9_]+\s*\(", re.MULTILINE)
_PYTEST_CLASS_PATTERN: Final = re.compile(r"^\s*class\s+Test[A-Za-z0-9_]*\s*[(:]", re.MULTILINE)
_PYTEST_RAN_MARKER: Final = re.compile(r"in \d+\.\d+s")
# Captures a module's own test function names so a module-level collection
# error (see _MODULE_COLLECTION_ERROR_NAME below) can be attributed back to
# the specific tests it would have contained.
_TEST_FUNCTION_NAME_PATTERN: Final = re.compile(
    r"^\s*(?:async\s+)?def\s+(test_[A-Za-z0-9_]+)\s*\(", re.MULTILINE
)
# pytest reports a module-level collection failure (bad import, syntax
# error) as ONE synthetic <testcase> named after the FILE itself (e.g.
# "test_generated_0", or "tests.test_generated_0" since run_tests() makes
# "tests" a real package via __init__.py) - never the real test function(s)
# that file was supposed to contain.
_MODULE_COLLECTION_ERROR_NAME: Final = re.compile(r"(?:^|\.)test_generated_(\d+)$")
_DEFAULT_TIMEOUT_SECONDS: Final = 120
_TEST_DOUBLE_PATTERN: Final = re.compile(
    r"\b(?:unittest\.mock|MagicMock|Mock\s*\(|patch\s*\(|monkeypatch\b|respx\b|responses\b)"
)
_RUNTIME_URL_NAMES: Final = ("MISSION_BACKEND_URL", "MISSION_FRONTEND_URL")


def extract_test_modules(output_text: str) -> list[str]:
    """Returns the raw text of every fenced ``python`` block in ``output_text``.

    Never invents test code: each returned string is exactly what the Test
    Generation Agent itself wrote for one test module.
    """

    return [body.strip("\n") for body in _FENCE_PATTERN.findall(output_text)]


def has_pytest_discoverable_tests(modules: list[str]) -> bool:
    """Returns whether generated modules contain at least one pytest test declaration."""

    return any(
        _PYTEST_FUNCTION_PATTERN.search(module) or _PYTEST_CLASS_PATTERN.search(module)
        for module in modules
    )


def validate_real_action_tests(modules: list[str]) -> tuple[str, ...]:
    """Returns fail-closed reasons when live acceptance tests use doubles or no live URL."""

    combined = "\n".join(modules)
    reasons: list[str] = []
    if _TEST_DOUBLE_PATTERN.search(combined):
        reasons.append("Acceptance tests contain a mock, patch, or interception library.")
    if not any(name in combined for name in _RUNTIME_URL_NAMES):
        reasons.append(
            "Acceptance tests do not reference MISSION_BACKEND_URL or MISSION_FRONTEND_URL."
        )
    return tuple(reasons)


@dataclass(frozen=True)
class TestExecutionResult:
    """The real, observed outcome of running the generated test suite."""

    __test__ = False

    ran: bool
    exit_code: int | None = None
    passed: int = 0
    failed: int = 0
    errors: int = 0
    summary: str = ""
    raw_output: str = field(default="", repr=False)
    passed_test_names: tuple[str, ...] = ()
    failed_test_names: tuple[str, ...] = ()
    errored_test_names: tuple[str, ...] = ()
    skipped_test_names: tuple[str, ...] = ()
    # True only when the pytest subprocess itself was killed for exceeding
    # ``timeout_seconds`` before it could finish (and, therefore, before it
    # could write any JUnit XML at all). Distinct from every other failure
    # mode: the requirement fidelity layer must not describe this as "no
    # JUnit result" for every requirement (which reads as a test-name
    # mismatch bug) - it genuinely never got a chance to run to completion.
    timed_out: bool = False

    @property
    def success(self) -> bool:
        """A generated test suite only counts as a real pass if it actually
        ran at least one test with no failures or errors - pytest exits
        successfully (code 0) even when it collects zero test functions
        (e.g. a generated module with no ``test_``-prefixed functions), which
        must never be silently treated as "the tests passed"."""

        return self.ran and self.errors == 0 and self.failed == 0 and self.passed > 0


class TestExecutionService:
    """Materializes and actually runs a mission's generated test suite."""

    __test__ = False

    def __init__(self, *, timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS) -> None:
        self._timeout_seconds = timeout_seconds

    @property
    def timeout_seconds(self) -> int:
        return self._timeout_seconds

    async def run_tests(
        self,
        *,
        build_root: Path,
        test_output_text: str,
        runtime_environment: dict[str, str] | None = None,
    ) -> TestExecutionResult:
        """Writes ``test_output_text``'s fenced test modules into ``build_root``/tests
        and actually executes them with pytest in an isolated subprocess."""

        modules = extract_test_modules(test_output_text)
        if not modules:
            return TestExecutionResult(
                ran=False, summary="No test code was found in the generated test suite."
            )

        build_root = build_root.resolve()
        tests_dir = build_root / "tests"
        if tests_dir.exists():
            shutil.rmtree(tests_dir)
        tests_dir.mkdir(parents=True, exist_ok=True)
        (tests_dir / "__init__.py").write_text("", encoding="utf-8")
        for index, module_text in enumerate(modules):
            (tests_dir / f"test_generated_{index}.py").write_text(module_text, encoding="utf-8")

        env = {"PATH": os.environ.get("PATH", "")}
        if sys.platform == "win32":
            env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
        explicit_environment = dict(runtime_environment or {})
        env.update(explicit_environment)

        junit_path = tests_dir / "pytest-results.xml"
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "pytest",
            str(tests_dir),
            "-q",
            f"--junitxml={junit_path}",
            # Without this, pytest's default behavior is to run NO tests at
            # all when ANY generated module fails to collect (e.g. one
            # module imports a package that isn't installed) - silently
            # turning one bad module into "no JUnit result" for every
            # requirement, not just the ones covered by that module.
            "--continue-on-collection-errors",
            cwd=str(build_root),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(), timeout=self._timeout_seconds
            )
        except TimeoutError:
            # A hanging generated test must never be left running as an
            # orphaned subprocess after this reports back to the caller.
            process.kill()
            await process.wait()
            return TestExecutionResult(
                ran=True,
                timed_out=True,
                summary=f"Test execution timed out after {self._timeout_seconds}s.",
            )

        raw_output = stdout.decode("utf-8", errors="replace")
        sensitive_values = {
            value
            for name, value in (runtime_environment or {}).items()
            if value
            and any(
                marker in name.upper()
                for marker in ("TOKEN", "SECRET", "PASSWORD", "KEY")
            )
        }
        for sensitive_value in sensitive_values:
            raw_output = raw_output.replace(sensitive_value, "[REDACTED]")
        outcomes = self._read_junit_outcomes(junit_path, modules)
        return self._summarize(
            raw_output=raw_output,
            exit_code=process.returncode,
            **outcomes,
        )

    @staticmethod
    def _read_junit_outcomes(junit_path: Path, modules: list[str]) -> dict[str, tuple[str, ...]]:
        outcomes: dict[str, list[str]] = {
            "passed_test_names": [],
            "failed_test_names": [],
            "errored_test_names": [],
            "skipped_test_names": [],
        }
        if not junit_path.exists():
            return {name: tuple(values) for name, values in outcomes.items()}
        try:
            root = ET.parse(junit_path).getroot()
        except ET.ParseError:
            return {name: tuple(values) for name, values in outcomes.items()}
        finally:
            junit_path.unlink(missing_ok=True)

        for test_case in root.iter("testcase"):
            test_name = test_case.attrib.get("name", "").strip()
            if not test_name:
                continue
            collection_error_match = _MODULE_COLLECTION_ERROR_NAME.search(test_name)
            if collection_error_match and test_case.find("error") is not None:
                # Without this, every requirement covered by this one broken
                # module would wrongly read as "no JUnit result" (as if its
                # test simply never ran) instead of the real, actionable
                # "errored" outcome - because pytest never reports the real
                # test function names for a module that failed to collect.
                module_index = int(collection_error_match.group(1))
                if 0 <= module_index < len(modules):
                    outcomes["errored_test_names"].extend(
                        _TEST_FUNCTION_NAME_PATTERN.findall(modules[module_index])
                    )
                    continue
            if test_case.find("failure") is not None:
                outcomes["failed_test_names"].append(test_name)
            elif test_case.find("error") is not None:
                outcomes["errored_test_names"].append(test_name)
            elif test_case.find("skipped") is not None:
                outcomes["skipped_test_names"].append(test_name)
            else:
                outcomes["passed_test_names"].append(test_name)
        return {name: tuple(values) for name, values in outcomes.items()}

    def _summarize(
        self,
        *,
        raw_output: str,
        exit_code: int | None,
        passed_test_names: tuple[str, ...] = (),
        failed_test_names: tuple[str, ...] = (),
        errored_test_names: tuple[str, ...] = (),
        skipped_test_names: tuple[str, ...] = (),
    ) -> TestExecutionResult:
        """Parses one completed pytest subprocess invocation's output.

        Split out from ``run_tests`` so this parsing logic - in particular,
        telling a real pytest run apart from pytest never having launched at
        all - is directly unit-testable against synthetic output, without
        needing an actual broken Python environment to reproduce it.
        """

        if not _PYTEST_RAN_MARKER.search(raw_output):
            # pytest always prints a final summary line ending in "in
            # <N.NN>s" (e.g. "1 passed in 0.04s", "no tests ran in 0.03s")
            # once it actually launches - even in quiet ("-q") mode. Its
            # total absence means pytest itself never ran (e.g.
            # `ModuleNotFoundError: No module named pytest` in a runtime
            # environment where pytest isn't installed, or some other
            # interpreter-level failure before pytest could start). This
            # must never be reported as "the generated test suite has no
            # runnable test function(s)" - that wrongly blames the
            # LLM-generated code for what is really an environment problem.
            return TestExecutionResult(
                ran=False,
                exit_code=exit_code,
                summary=(
                    f"pytest did not run (exit code {exit_code}): "
                    f"{raw_output.strip()[-500:] or '(no output)'}"
                ),
                raw_output=raw_output,
            )

        passed_match = _PASSED_PATTERN.search(raw_output)
        failed_match = _FAILED_PATTERN.search(raw_output)
        errors_match = _ERRORS_PATTERN.search(raw_output)
        passed = int(passed_match.group(1)) if passed_match else 0
        failed = int(failed_match.group(1)) if failed_match else 0
        errors = int(errors_match.group(1)) if errors_match else 0

        summary = f"{passed} passed, {failed} failed, {errors} errors"
        if passed == 0 and failed == 0 and errors == 0:
            # pytest exits 0 ("success") even when it collects zero test
            # functions - e.g. a generated module containing only helper
            # functions with no `test_` prefix. That must never read as a
            # real pass: nothing was actually verified.
            summary = (
                "No tests were collected - the generated test suite has no "
                "runnable test function(s)."
            )

        return TestExecutionResult(
            ran=True,
            exit_code=exit_code,
            passed=passed,
            failed=failed,
            errors=errors,
            summary=summary,
            raw_output=raw_output,
            passed_test_names=passed_test_names,
            failed_test_names=failed_test_names,
            errored_test_names=errored_test_names,
            skipped_test_names=skipped_test_names,
        )
