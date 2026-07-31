"""Real, sandboxed execution of the Test Generation Agent's own generated tests.

Extracts every fenced ``python`` code block from the ``generate-test-suite``
step's own ``output_text`` (the Test Generation Agent's real test code - see
``test-generation-v1`` in ``config/prompts/registry.yaml``), writes each as
a real file alongside the materialized build, and actually executes them
with ``pytest`` in a subprocess - a real, observed pass/fail outcome, never
a fabricated "tests passed" result. A generated test suite that fails to
import or run against its own generated build is reported as a real
failure, not silently treated as success.

The subprocess runs with a stripped environment (only ``PATH`` and, on
Windows, ``SYSTEMROOT`` - no ambient credentials/secrets are passed
through), a bounded timeout, and its own dedicated working directory, so an
untrusted/LLM-generated test suite can never read this process's secrets
or escape its sandbox directory.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

__all__ = ["TestExecutionResult", "TestExecutionService", "extract_test_modules"]

_FENCE_PATTERN: Final = re.compile(r"```python\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_PASSED_PATTERN: Final = re.compile(r"(\d+) passed")
_FAILED_PATTERN: Final = re.compile(r"(\d+) failed")
_ERRORS_PATTERN: Final = re.compile(r"(\d+) error(?:s)?")
_DEFAULT_TIMEOUT_SECONDS: Final = 120


def extract_test_modules(output_text: str) -> list[str]:
    """Returns the raw text of every fenced ``python`` block in ``output_text``.

    Never invents test code: each returned string is exactly what the Test
    Generation Agent itself wrote for one test module.
    """

    return [body.strip("\n") for body in _FENCE_PATTERN.findall(output_text)]


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


class TestExecutionService:
    """Materializes and actually runs a mission's generated test suite."""

    __test__ = False

    def __init__(self, *, timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS) -> None:
        self._timeout_seconds = timeout_seconds

    async def run_tests(self, *, build_root: Path, test_output_text: str) -> TestExecutionResult:
        """Writes ``test_output_text``'s fenced test modules into ``build_root``/tests
        and actually executes them with pytest in a sandboxed subprocess."""

        modules = extract_test_modules(test_output_text)
        if not modules:
            return TestExecutionResult(
                ran=False, summary="No test code was found in the generated test suite."
            )

        tests_dir = build_root / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        (tests_dir / "__init__.py").write_text("", encoding="utf-8")
        for index, module_text in enumerate(modules):
            (tests_dir / f"test_generated_{index}.py").write_text(module_text, encoding="utf-8")

        env = {"PATH": os.environ.get("PATH", "")}
        if sys.platform == "win32":
            env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")

        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "pytest",
                str(tests_dir),
                "-q",
                cwd=str(build_root),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(
                process.communicate(), timeout=self._timeout_seconds
            )
        except TimeoutError:
            return TestExecutionResult(
                ran=True, summary=f"Test execution timed out after {self._timeout_seconds}s."
            )

        raw_output = stdout.decode("utf-8", errors="replace")
        passed_match = _PASSED_PATTERN.search(raw_output)
        failed_match = _FAILED_PATTERN.search(raw_output)
        errors_match = _ERRORS_PATTERN.search(raw_output)
        passed = int(passed_match.group(1)) if passed_match else 0
        failed = int(failed_match.group(1)) if failed_match else 0
        errors = int(errors_match.group(1)) if errors_match else 0

        return TestExecutionResult(
            ran=True,
            exit_code=process.returncode,
            passed=passed,
            failed=failed,
            errors=errors,
            summary=f"{passed} passed, {failed} failed, {errors} errors",
            raw_output=raw_output,
        )
