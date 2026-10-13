"""Unit tests for UiTestExecutionService (real sandboxed vitest + playwright execution).

Mirrors test_test_execution_service.py's own discipline: no mocking of the
service under test itself - these tests actually run `npm install`, vitest,
and (for the end-to-end path) a real Playwright browser against a tiny
materialized frontend fixture, and assert on the real, observed result.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import subprocess
import sys
from pathlib import Path

import httpx

from app.deploy_launch.pipeline_service import (
    _FRONTEND_PACKAGE_JSON,
    _FRONTEND_PLAYWRIGHT_CONFIG,
    _FRONTEND_VITE_CONFIG,
    _FRONTEND_VITEST_CONFIG,
    _FRONTEND_VITEST_SETUP,
)
from app.deploy_launch.ui_test_execution_service import (
    UiTestExecutionService,
    extract_playwright_test_modules,
    extract_ui_unit_test_modules,
    has_playwright_discoverable_tests,
    has_vitest_discoverable_tests,
)

_PASSING_UNIT_TEST = """
```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

function Greeting() {
  return <div>Hello, Genie</div>;
}

describe("Greeting", () => {
  it("renders the greeting text", () => {
    render(<Greeting />);
    expect(screen.getByText("Hello, Genie")).toBeInTheDocument();
  });
});
```
"""

_FAILING_UNIT_TEST = """
```tsx
import { describe, expect, it } from "vitest";

describe("broken", () => {
  it("fails on purpose", () => {
    expect(1).toBe(2);
  });
});
```
"""

_NO_TEST_CALLS = """
```tsx
export function helperNotATest() {
  return 1 + 1;
}
```
"""

_PASSING_PLAYWRIGHT_TEST = """
```playwright
import { test, expect } from "@playwright/test";

test("home page shows the mission title", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("h1")).toHaveText("Fixture Mission");
});
```
"""


def _materialize_minimal_frontend(frontend_root: Path) -> None:
    """Writes just enough of a real generated-mission frontend (package.json,
    vite/vitest/playwright config, a tiny React entrypoint) for npm install
    and vitest/playwright to actually run against it - the same scaffold
    pipeline_service.py's own "sync-frontend-integration" step materializes."""

    frontend_root.mkdir(parents=True, exist_ok=True)
    (frontend_root / "package.json").write_text(_FRONTEND_PACKAGE_JSON, encoding="utf-8")
    (frontend_root / "vite.config.ts").write_text(_FRONTEND_VITE_CONFIG, encoding="utf-8")
    (frontend_root / "vitest.config.ts").write_text(_FRONTEND_VITEST_CONFIG, encoding="utf-8")
    (frontend_root / "playwright.config.ts").write_text(
        _FRONTEND_PLAYWRIGHT_CONFIG, encoding="utf-8"
    )
    (frontend_root / "tsconfig.json").write_text(
        '{"compilerOptions": {"jsx": "react-jsx", "esModuleInterop": true, '
        '"skipLibCheck": true, "moduleResolution": "Bundler", "module": "ESNext"}}',
        encoding="utf-8",
    )
    src_root = frontend_root / "src"
    src_root.mkdir(parents=True, exist_ok=True)
    (src_root / "test-setup.ts").write_text(_FRONTEND_VITEST_SETUP, encoding="utf-8")
    (src_root / "main.tsx").write_text(
        "import React from 'react';\n"
        "import { createRoot } from 'react-dom/client';\n"
        "function App() { return <h1>Fixture Mission</h1>; }\n"
        "createRoot(document.getElementById('root')!).render(<App />);\n",
        encoding="utf-8",
    )
    (frontend_root / "index.html").write_text(
        '<!doctype html><html><head><title>Fixture</title></head>'
        '<body><div id="root"></div><script type="module" src="/src/main.tsx"></script>'
        "</body></html>",
        encoding="utf-8",
    )


def test_extract_ui_unit_test_modules_returns_each_fenced_tsx_block():
    modules = extract_ui_unit_test_modules(_PASSING_UNIT_TEST + _FAILING_UNIT_TEST)

    assert len(modules) == 2
    assert "renders the greeting text" in modules[0]
    assert "fails on purpose" in modules[1]


def test_extract_ui_unit_test_modules_returns_empty_list_for_narrative_only_text():
    assert extract_ui_unit_test_modules("Here is a narrative description with no code.") == []


def test_extract_playwright_test_modules_returns_each_fenced_block():
    modules = extract_playwright_test_modules(_PASSING_PLAYWRIGHT_TEST)

    assert len(modules) == 1
    assert "home page shows the mission title" in modules[0]


def test_extract_functions_do_not_cross_contaminate_fence_languages():
    combined = _PASSING_UNIT_TEST + _PASSING_PLAYWRIGHT_TEST
    assert len(extract_ui_unit_test_modules(combined)) == 1
    assert len(extract_playwright_test_modules(combined)) == 1


def test_detects_vitest_discoverable_test_and_rejects_helper_only_module():
    assert has_vitest_discoverable_tests(extract_ui_unit_test_modules(_PASSING_UNIT_TEST))
    assert not has_vitest_discoverable_tests(extract_ui_unit_test_modules(_NO_TEST_CALLS))


def test_detects_playwright_discoverable_test():
    assert has_playwright_discoverable_tests(
        extract_playwright_test_modules(_PASSING_PLAYWRIGHT_TEST)
    )
    assert not has_playwright_discoverable_tests([])


async def test_run_ui_tests_reports_not_ran_when_output_has_no_code(tmp_path: Path):
    service = UiTestExecutionService()

    result = await service.run_ui_tests(frontend_root=tmp_path, test_output_text="just narrative")

    assert result.unit_ran is False
    assert result.e2e_ran is False
    assert result.success is False
    assert "No generated UI tests" in result.summary


async def test_run_ui_tests_actually_executes_generated_vitest_module(tmp_path: Path):
    """Real npm install + real vitest subprocess - no mocking of the service
    under test. Marked separately from the fast unit tests above since it
    depends on npm registry access and takes real wall-clock time."""

    _materialize_minimal_frontend(tmp_path)
    service = UiTestExecutionService(
        npm_install_timeout_seconds=600, vitest_timeout_seconds=120
    )

    result = await service.run_ui_tests(
        frontend_root=tmp_path, test_output_text=_PASSING_UNIT_TEST
    )

    assert result.npm_install_failed is False, result.npm_install_output[-2000:]
    assert result.unit_ran is True
    assert result.unit_passed == 1
    assert result.unit_failed == 0
    assert result.e2e_ran is False
    assert result.success is True
    assert (tmp_path / "src" / "__tests__" / "generated_0.test.tsx").exists()


async def test_run_ui_tests_reports_real_vitest_failures_not_fabricated_success(
    tmp_path: Path,
):
    _materialize_minimal_frontend(tmp_path)
    service = UiTestExecutionService(
        npm_install_timeout_seconds=600, vitest_timeout_seconds=120
    )

    result = await service.run_ui_tests(
        frontend_root=tmp_path, test_output_text=_FAILING_UNIT_TEST
    )

    assert result.npm_install_failed is False, result.npm_install_output[-2000:]
    assert result.unit_ran is True
    assert result.unit_failed == 1
    assert result.success is False


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextlib.asynccontextmanager
async def _real_vite_dev_server(frontend_root: Path):
    """Actually serves the materialized fixture frontend with `vite dev` so
    the Playwright test below has a real, live `baseURL` to navigate to -
    the same way a deployed mission's real MISSION_FRONTEND_URL works, just
    local. Never mocked: a real Node subprocess, a real HTTP server.

    Deliberately uses the synchronous `subprocess.Popen` (not
    `asyncio.create_subprocess_exec`) for this long-lived background
    process: a real, observed incident while building this test - starting
    the dev server via asyncio's own subprocess API on Windows, then having
    `UiTestExecutionService` spawn ITS OWN concurrent asyncio subprocesses
    against the same ProactorEventLoop while the dev server was still
    running, made every subsequent subprocess call hang indefinitely (each
    individual command ran in well under a minute when tried in isolation).
    `subprocess.Popen` sidesteps the loop entirely for this one
    test-harness-only background process.

    Requires `npm install` to have already run in ``frontend_root`` (vite
    itself is a devDependency, not a globally available binary)."""

    port = _free_tcp_port()
    npx_command = "npx.cmd" if sys.platform == "win32" else "npx"
    process = subprocess.Popen(
        [npx_command, "vite", "dev", "--port", str(port), "--strictPort"],
        cwd=str(frontend_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        async with httpx.AsyncClient() as client:
            for _ in range(60):
                if process.poll() is not None:
                    output = process.stdout.read() if process.stdout else ""
                    raise RuntimeError(
                        f"vite dev exited early (code {process.returncode}): {output[-2000:]}"
                    )
                try:
                    response = await client.get(base_url, timeout=2.0)
                    if response.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(1.0)
            else:
                raise RuntimeError("vite dev server never became ready.")
        yield base_url
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


async def test_run_ui_tests_actually_drives_a_real_browser_against_a_real_server(
    tmp_path: Path,
):
    """Real npm install, a real `vite dev` server, a real downloaded Chromium,
    and a real Playwright browser navigating to it - proves the end-to-end
    half of Phase A (the part nothing in Genie's pipeline executed before
    this change) genuinely works, not just that the subprocess plumbing is
    well-typed."""

    _materialize_minimal_frontend(tmp_path)
    npm_command = "npm.cmd" if sys.platform == "win32" else "npm"
    install_process = await asyncio.create_subprocess_exec(
        npm_command, "install", cwd=str(tmp_path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    install_output, _ = await asyncio.wait_for(install_process.communicate(), timeout=300)
    assert install_process.returncode == 0, install_output.decode("utf-8", errors="replace")[-2000:]

    service = UiTestExecutionService(
        npm_install_timeout_seconds=60,  # already installed above; should be a near-instant no-op
        playwright_install_timeout_seconds=600,
        playwright_test_timeout_seconds=120,
    )

    async with _real_vite_dev_server(tmp_path) as base_url:
        result = await service.run_ui_tests(
            frontend_root=tmp_path,
            test_output_text=_PASSING_PLAYWRIGHT_TEST,
            runtime_environment={"MISSION_FRONTEND_URL": base_url},
        )

    assert result.npm_install_failed is False, result.npm_install_output[-2000:]
    assert result.e2e_ran is True, result.raw_e2e_output[-2000:]
    assert result.e2e_passed == 1, result.raw_e2e_output[-2000:]
    assert result.e2e_failed == 0
    assert result.unit_ran is False
    assert result.success is True
    assert (tmp_path / "tests" / "e2e" / "generated_0.spec.ts").exists()
