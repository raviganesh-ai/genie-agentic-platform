"""Unit tests for the Phase E reachability pre-flight (probe_reachability) and
its wiring into BackendDeploymentService (_probe_existing_backend_reachability,
_wait_for_readiness's severity-distinguishing error message).

probe_reachability is tested against a real local HTTP server (no mocking
of httpx) - the same discipline used throughout this codebase's other real
subprocess/network checks (DependencyPreflightService, UiTestExecutionService).
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from app.deploy_launch.backend_deployment_service import (
    BackendDeploymentError,
    BackendDeploymentService,
)
from app.deploy_launch.reachability_preflight import probe_reachability


class _ReadyHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's own naming convention
        if self.path == "/health/ready":
            body = b'{"status": "ready"}'
            self.send_response(200)
        else:
            body = b'{"status": "error"}'
            self.send_response(503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # silence default stderr request logging
        return


@pytest.fixture
def real_ready_server():
    server = HTTPServer(("127.0.0.1", 0), _ReadyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


async def test_probe_reachability_returns_true_for_a_real_healthy_endpoint(real_ready_server):
    assert await probe_reachability(real_ready_server) is True


async def test_probe_reachability_returns_false_for_an_unreachable_host():
    # Port 1 is reserved and nothing listens there - a real connection
    # failure, not a mocked one.
    assert await probe_reachability("http://127.0.0.1:1") is False


async def test_probe_reachability_returns_false_for_a_non_ready_response(real_ready_server):
    # Hitting a path this real server responds to with HTTP 503.
    assert await probe_reachability(f"{real_ready_server}/not-health") is False


async def test_probe_existing_backend_reachability_returns_none_when_app_does_not_exist():
    service = BackendDeploymentService(
        subscription_id="sub-123",
        resource_group="genie-dev-rg",
        acr_name="acr123",
        container_apps_environment_id="env-123",
        location="eastus2",
        foundry_endpoint="https://foundry.example.com/api/projects/demo",
        foundry_project_name="demo",
    )

    def _not_found(*_args):
        error = Exception("not found")
        error.status_code = 404
        raise error

    service._container_apps_client = lambda: SimpleNamespace(  # type: ignore[method-assign]
        container_apps=SimpleNamespace(get=_not_found)
    )

    result = await service._probe_existing_backend_reachability("claims-1234")

    assert result is None


async def test_probe_existing_backend_reachability_returns_true_for_a_real_healthy_existing_app(
    monkeypatch, real_ready_server
):
    host_and_port = real_ready_server.removeprefix("http://")
    service = BackendDeploymentService(
        subscription_id="sub-123",
        resource_group="genie-dev-rg",
        acr_name="acr123",
        container_apps_environment_id="env-123",
        location="eastus2",
        foundry_endpoint="https://foundry.example.com/api/projects/demo",
        foundry_project_name="demo",
    )

    # _probe_existing_backend_reachability always probes "https://{fqdn}" -
    # correct for real Container Apps ingress, but this test's local fixture
    # server has no TLS certificate to terminate HTTPS with. Rewriting the
    # scheme back to plain HTTP here only works around that local-test
    # limitation; the real probe_reachability function underneath still
    # makes one genuine HTTP round trip against a real running server, it
    # is never mocked.
    from app.deploy_launch import backend_deployment_service as backend_deployment_module

    real_probe = backend_deployment_module.probe_reachability

    async def probe_over_plain_http(url: str) -> bool:
        return await real_probe(url.replace("https://", "http://", 1))

    monkeypatch.setattr(backend_deployment_module, "probe_reachability", probe_over_plain_http)

    # Real Container Apps resources expose their ingress FQDN this exact
    # shape (`.configuration.ingress.fqdn`) - see deploy()'s own identical
    # access pattern immediately after begin_create_or_update().
    existing_app = SimpleNamespace(
        configuration=SimpleNamespace(ingress=SimpleNamespace(fqdn=host_and_port))
    )
    service._container_apps_client = lambda: SimpleNamespace(  # type: ignore[method-assign]
        container_apps=SimpleNamespace(get=lambda *_: existing_app)
    )

    result = await service._probe_existing_backend_reachability("claims-1234")

    assert result is True


async def test_wait_for_readiness_mentions_prior_live_traffic_when_pre_deploy_reachable(
    monkeypatch,
):
    import httpx

    class _AlwaysUnready:
        def __init__(self, *, timeout):
            del timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url):
            return SimpleNamespace(status_code=503, json=lambda: {"status": "error"})

    async def no_sleep(_seconds):
        return None

    from app.deploy_launch import backend_deployment_service as backend_deployment_module

    clock = iter((0, 0, 301))
    monkeypatch.setattr(httpx, "AsyncClient", _AlwaysUnready)
    monkeypatch.setattr(
        backend_deployment_module, "time", SimpleNamespace(monotonic=lambda: next(clock))
    )
    monkeypatch.setattr(backend_deployment_module, "asyncio", SimpleNamespace(sleep=no_sleep))
    service = BackendDeploymentService(
        subscription_id="sub-123",
        resource_group="genie-dev-rg",
        acr_name="acr123",
        container_apps_environment_id="env-123",
        location="eastus2",
        foundry_endpoint="https://foundry.example.com/api/projects/demo",
        foundry_project_name="demo",
    )

    with pytest.raises(BackendDeploymentError, match="may have just taken a live mission offline"):
        await service._wait_for_readiness(
            "https://claims-1234.azure-api.net", pre_deploy_reachable=True
        )


async def test_wait_for_readiness_omits_prior_live_traffic_warning_for_a_first_deploy(
    monkeypatch,
):
    import httpx

    class _AlwaysUnready:
        def __init__(self, *, timeout):
            del timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url):
            return SimpleNamespace(status_code=503, json=lambda: {"status": "error"})

    async def no_sleep(_seconds):
        return None

    from app.deploy_launch import backend_deployment_service as backend_deployment_module

    clock = iter((0, 0, 301))
    monkeypatch.setattr(httpx, "AsyncClient", _AlwaysUnready)
    monkeypatch.setattr(
        backend_deployment_module, "time", SimpleNamespace(monotonic=lambda: next(clock))
    )
    monkeypatch.setattr(backend_deployment_module, "asyncio", SimpleNamespace(sleep=no_sleep))
    service = BackendDeploymentService(
        subscription_id="sub-123",
        resource_group="genie-dev-rg",
        acr_name="acr123",
        container_apps_environment_id="env-123",
        location="eastus2",
        foundry_endpoint="https://foundry.example.com/api/projects/demo",
        foundry_project_name="demo",
    )

    with pytest.raises(BackendDeploymentError) as exc_info:
        await service._wait_for_readiness(
            "https://claims-1234.azure-api.net", pre_deploy_reachable=None
        )

    assert "may have just taken a live mission offline" not in str(exc_info.value)
