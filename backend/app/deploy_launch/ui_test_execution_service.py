"""Real, sandboxed execution of the Test Generation Agent's generated UI tests.

Phase A of the Genie platform-change roadmap ("why couldn't Genie produce
this level of prototype from the start"): the ``test-generation-v1`` prompt
(see ``config/prompts/registry.yaml``) already generates two kinds of UI
coverage for every mission - Vitest/React Testing Library unit tests (one
fenced ```tsx code block per screen) and a single Playwright end-to-end
smoke test of the mission's primary user journey (fenced ```playwright) -
but neither was ever actually executed by the deploy pipeline. Every real
bug found during the ACI PoC mission this session (premature validation
errors shown on page load, a catalog button that only copied text to the
clipboard, broken checkbox wiring, wrong MCP tool argument prefills) was a
frontend-interaction bug that ONLY a real browser driving the real deployed
UI could have caught - the existing backend black-box acceptance tests
(``app.deploy_launch.test_execution_service``) never touch the DOM at all.

This module extracts both kinds of generated UI test code, writes them into
the real materialized frontend build, and actually executes them with
``npm run test:unit`` (vitest) and ``npm run test:e2e`` (playwright) in
subprocesses - real, observed pass/fail outcomes against the real deployed
mission, never a fabricated "tests passed" result. Mirrors
``TestExecutionService``'s own "never trust an absent/malformed result"
discipline throughout.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

__all__ = [
    "UiTestExecutionResult",
    "UiTestExecutionService",
    "extract_ui_unit_test_modules",
    "extract_playwright_test_modules",
    "has_vitest_discoverable_tests",
    "has_playwright_discoverable_tests",
]

# Same line-anchored fence convention as test_execution_service.extract_test_modules
# (triple backticks inside a generated test's own string literals cannot
# terminate the block early).
_TSX_FENCE_PATTERN: Final = re.compile(
    r"^[ \t]*```tsx[ \t]*\n(.*?)^[ \t]*```[ \t]*$",
    re.DOTALL | re.IGNORECASE | re.MULTILINE,
)
_PLAYWRIGHT_FENCE_PATTERN: Final = re.compile(
    r"^[ \t]*```playwright[ \t]*\n(.*?)^[ \t]*```[ \t]*$",
    re.DOTALL | re.IGNORECASE | re.MULTILINE,
)
_VITEST_TEST_PATTERN: Final = re.compile(
    r"\b(?:it|test)\s*(?:\.\s*(?:only|skip))?\s*\(", re.MULTILINE
)
_PLAYWRIGHT_TEST_PATTERN: Final = re.compile(r"\btest\s*(?:\.\s*(?:only|skip))?\s*\(", re.MULTILINE)
_DEFAULT_NPM_INSTALL_TIMEOUT_SECONDS: Final = 300
_DEFAULT_VITEST_TIMEOUT_SECONDS: Final = 180
_DEFAULT_PLAYWRIGHT_INSTALL_TIMEOUT_SECONDS: Final = 300
_DEFAULT_PLAYWRIGHT_TEST_TIMEOUT_SECONDS: Final = 180
_NPM_COMMAND: Final = "npm.cmd" if sys.platform == "win32" else "npm"
_NPX_COMMAND: Final = "npx.cmd" if sys.platform == "win32" else "npx"


def extract_ui_unit_test_modules(output_text: str) -> list[str]:
    """Returns the raw text of every fenced ```tsx block in ``output_text``.

    Never invents test code: each returned string is exactly what the Test
    Generation Agent itself wrote for one screen's Vitest/RTL test module.
    """

    return [body.strip("\n") for body in _TSX_FENCE_PATTERN.findall(output_text)]


def extract_playwright_test_modules(output_text: str) -> list[str]:
    """Returns the raw text of every fenced ```playwright block in ``output_text``.

    Never invents test code: each returned string is exactly what the Test
    Generation Agent itself wrote for the mission's primary-journey smoke test.
    """

    return [body.strip("\n") for body in _PLAYWRIGHT_FENCE_PATTERN.findall(output_text)]


def has_vitest_discoverable_tests(modules: list[str]) -> bool:
    """Returns whether generated modules contain at least one vitest `it(`/`test(` call."""

    return any(_VITEST_TEST_PATTERN.search(module) for module in modules)


def has_playwright_discoverable_tests(modules: list[str]) -> bool:
    """Returns whether generated modules contain at least one playwright `test(` call."""

    return any(_PLAYWRIGHT_TEST_PATTERN.search(module) for module in modules)


@dataclass(frozen=True)
class UiTestExecutionResult:
    """The real, observed outcome of running the generated UI test suites.

    Combines both the Vitest/RTL unit-test run and the Playwright end-to-end
    run into one result - a mission with no generated UI tests at all (e.g.
    an older ``generate-test-suite`` output predating Phase A) reports
    ``ran=False`` for the corresponding half rather than a fabricated pass.
    """

    __test__ = False

    unit_ran: bool = False
    unit_passed: int = 0
    unit_failed: int = 0
    unit_summary: str = ""
    unit_timed_out: bool = False
    e2e_ran: bool = False
    e2e_passed: int = 0
    e2e_failed: int = 0
    e2e_summary: str = ""
    e2e_timed_out: bool = False
    npm_install_failed: bool = False
    npm_install_output: str = field(default="", repr=False)
    raw_unit_output: str = field(default="", repr=False)
    raw_e2e_output: str = field(default="", repr=False)

    @property
    def success(self) -> bool:
        """A real pass only when at least one suite actually ran, both
        halves that did run had zero failures, and ``npm install`` itself
        did not fail (which would otherwise make every subsequent step
        trivially report "0 ran, 0 failed" and read as a false pass)."""

        if self.npm_install_failed:
            return False
        if not self.unit_ran and not self.e2e_ran:
            return False
        if self.unit_ran and self.unit_failed > 0:
            return False
        if self.e2e_ran and self.e2e_failed > 0:
            return False
        if self.unit_ran and self.unit_passed == 0:
            return False
        if self.e2e_ran and self.e2e_passed == 0:
            return False
        return True

    @property
    def summary(self) -> str:
        if self.npm_install_failed:
            return f"npm install failed: {self.npm_install_output.strip()[-500:]}"
        parts = []
        if self.unit_ran or self.unit_summary:
            parts.append(f"unit: {self.unit_summary}")
        if self.e2e_ran or self.e2e_summary:
            parts.append(f"e2e: {self.e2e_summary}")
        return "; ".join(parts) if parts else "No generated UI tests were found."


class UiTestExecutionService:
    """Materializes and actually runs a mission's generated UI test suites."""

    __test__ = False

    def __init__(
        self,
        *,
        npm_install_timeout_seconds: int = _DEFAULT_NPM_INSTALL_TIMEOUT_SECONDS,
        vitest_timeout_seconds: int = _DEFAULT_VITEST_TIMEOUT_SECONDS,
        playwright_install_timeout_seconds: int = _DEFAULT_PLAYWRIGHT_INSTALL_TIMEOUT_SECONDS,
        playwright_test_timeout_seconds: int = _DEFAULT_PLAYWRIGHT_TEST_TIMEOUT_SECONDS,
    ) -> None:
        self._npm_install_timeout_seconds = npm_install_timeout_seconds
        self._vitest_timeout_seconds = vitest_timeout_seconds
        self._playwright_install_timeout_seconds = playwright_install_timeout_seconds
        self._playwright_test_timeout_seconds = playwright_test_timeout_seconds

    async def run_ui_tests(
        self,
        *,
        frontend_root: Path,
        test_output_text: str,
        runtime_environment: dict[str, str] | None = None,
    ) -> UiTestExecutionResult:
        """Writes ``test_output_text``'s fenced UI test modules into
        ``frontend_root`` and actually executes them with vitest and
        playwright in isolated subprocesses."""

        unit_modules = extract_ui_unit_test_modules(test_output_text)
        e2e_modules = extract_playwright_test_modules(test_output_text)
        if not unit_modules and not e2e_modules:
            return UiTestExecutionResult()

        frontend_root = frontend_root.resolve()
        env = self._build_subprocess_env(runtime_environment)

        install_ok, install_output = await self._npm_install(frontend_root, env)
        if not install_ok:
            return UiTestExecutionResult(npm_install_failed=True, npm_install_output=install_output)

        unit_result = (
            await self._run_vitest(frontend_root, unit_modules, env)
            if unit_modules
            else (False, 0, 0, "", "", False)
        )
        e2e_result = (
            await self._run_playwright(frontend_root, e2e_modules, env)
            if e2e_modules
            else (False, 0, 0, "", "", False)
        )

        return UiTestExecutionResult(
            unit_ran=unit_result[0],
            unit_passed=unit_result[1],
            unit_failed=unit_result[2],
            unit_summary=unit_result[3],
            raw_unit_output=unit_result[4],
            unit_timed_out=unit_result[5],
            e2e_ran=e2e_result[0],
            e2e_passed=e2e_result[1],
            e2e_failed=e2e_result[2],
            e2e_summary=e2e_result[3],
            raw_e2e_output=e2e_result[4],
            e2e_timed_out=e2e_result[5],
        )

    @staticmethod
    def _build_subprocess_env(
        runtime_environment: dict[str, str] | None,
    ) -> dict[str, str]:
        # Same stripped-environment discipline as TestExecutionService: only
        # PATH, Windows' SYSTEMROOT, and explicitly allow-listed mission URLs
        # are visible to the subprocess - never the ambient process's full
        # environment (which could leak unrelated secrets into generated
        # test output).
        env = {"PATH": os.environ.get("PATH", "")}
        if sys.platform == "win32":
            env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
            # npm/node on Windows resolve global config and the npm cache
            # through these - without them `npm install` fails outright
            # with ENOENT-style errors before ever reaching package
            # resolution.
            for name in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "TEMP", "TMP"):
                value = os.environ.get(name)
                if value:
                    env[name] = value
        env.update(runtime_environment or {})
        return env

    async def _npm_install(
        self, frontend_root: Path, env: dict[str, str]
    ) -> tuple[bool, str]:
        process = await asyncio.create_subprocess_exec(
            _NPM_COMMAND,
            "install",
            cwd=str(frontend_root),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(), timeout=self._npm_install_timeout_seconds
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            return False, f"npm install timed out after {self._npm_install_timeout_seconds}s."
        output = stdout.decode("utf-8", errors="replace")
        return process.returncode == 0, output

    async def _run_vitest(
        self, frontend_root: Path, modules: list[str], env: dict[str, str]
    ) -> tuple[bool, int, int, str, str, bool]:
        tests_dir = frontend_root / "src" / "__tests__"
        tests_dir.mkdir(parents=True, exist_ok=True)
        for existing in tests_dir.glob("generated_*.test.tsx"):
            existing.unlink()
        for index, module_text in enumerate(modules):
            (tests_dir / f"generated_{index}.test.tsx").write_text(module_text, encoding="utf-8")

        junit_path = frontend_root / "vitest-results.xml"
        junit_path.unlink(missing_ok=True)
        process = await asyncio.create_subprocess_exec(
            _NPM_COMMAND,
            "run",
            "test:unit",
            cwd=str(frontend_root),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(), timeout=self._vitest_timeout_seconds
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            return (
                True,
                0,
                0,
                f"vitest timed out after {self._vitest_timeout_seconds}s.",
                "",
                True,
            )
        raw_output = stdout.decode("utf-8", errors="replace")
        passed, failed, summary = _read_junit_summary(junit_path, raw_output, tool="vitest")
        return True, passed, failed, summary, raw_output, False

    async def _run_playwright(
        self, frontend_root: Path, modules: list[str], env: dict[str, str]
    ) -> tuple[bool, int, int, str, str, bool]:
        e2e_dir = frontend_root / "tests" / "e2e"
        e2e_dir.mkdir(parents=True, exist_ok=True)
        for existing in e2e_dir.glob("generated_*.spec.ts"):
            existing.unlink()
        for index, module_text in enumerate(modules):
            (e2e_dir / f"generated_{index}.spec.ts").write_text(module_text, encoding="utf-8")

        # A real, observed incident elsewhere in this platform (see git
        # history: a generated mission's own Dockerfile hung 20+ minutes on
        # puppeteer's Chromium postinstall download) makes this install a
        # real risk - bounded by its own dedicated timeout, with
        # ``--with-deps`` deliberately omitted (it requires root/apt access
        # this process does not have and is not needed to merely launch a
        # already-downloaded Chromium).
        install_process = await asyncio.create_subprocess_exec(
            _NPX_COMMAND,
            "playwright",
            "install",
            "chromium",
            cwd=str(frontend_root),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            install_stdout, _ = await asyncio.wait_for(
                install_process.communicate(),
                timeout=self._playwright_install_timeout_seconds,
            )
        except TimeoutError:
            install_process.kill()
            await install_process.wait()
            return (
                True,
                0,
                0,
                f"playwright browser install timed out after "
                f"{self._playwright_install_timeout_seconds}s.",
                "",
                True,
            )
        if install_process.returncode != 0:
            return (
                True,
                0,
                0,
                "playwright browser install failed: "
                + install_stdout.decode("utf-8", errors="replace").strip()[-500:],
                install_stdout.decode("utf-8", errors="replace"),
                False,
            )

        junit_path = frontend_root / "playwright-results.xml"
        junit_path.unlink(missing_ok=True)
        process = await asyncio.create_subprocess_exec(
            _NPM_COMMAND,
            "run",
            "test:e2e",
            cwd=str(frontend_root),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(), timeout=self._playwright_test_timeout_seconds
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            return (
                True,
                0,
                0,
                f"playwright test run timed out after "
                f"{self._playwright_test_timeout_seconds}s.",
                "",
                True,
            )
        raw_output = stdout.decode("utf-8", errors="replace")
        passed, failed, summary = _read_junit_summary(junit_path, raw_output, tool="playwright")
        return True, passed, failed, summary, raw_output, False


def _read_junit_summary(junit_path: Path, raw_output: str, *, tool: str) -> tuple[int, int, str]:
    """Parses a JUnit XML report shared by vitest's and playwright's own
    ``junit`` reporters (``<testsuite><testcase>``, ``<failure>``/``<error>``
    children) into (passed, failed, summary).

    Absence of the file is reported as a real zero-test outcome, never a
    fabricated pass - mirrors TestExecutionResult.success's own rule that
    pytest exiting 0 with nothing collected must never read as success.
    """

    if not junit_path.exists():
        return 0, 0, f"No {tool} JUnit report was produced: {raw_output.strip()[-500:] or '(no output)'}"
    try:
        root = ET.parse(junit_path).getroot()
    except ET.ParseError:
        return 0, 0, f"{tool}'s JUnit report could not be parsed."
    finally:
        junit_path.unlink(missing_ok=True)

    passed = 0
    failed = 0
    for test_case in root.iter("testcase"):
        if test_case.find("failure") is not None or test_case.find("error") is not None:
            failed += 1
        elif test_case.find("skipped") is not None:
            continue
        else:
            passed += 1

    if passed == 0 and failed == 0:
        return 0, 0, f"No {tool} tests were collected."
    return passed, failed, f"{passed} passed, {failed} failed"
