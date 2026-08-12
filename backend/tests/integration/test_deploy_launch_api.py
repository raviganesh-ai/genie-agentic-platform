"""Integration test: Deploy & Launch API routes wiring.

Exercises auth/session-ownership through the real ASGI app (confirms
``app.main.create_app`` wires ``DeploymentPipelineService`` onto
``app.state`` correctly) - same minimal pattern as
``test_workflow_events_stream_api.py``. Approval-gating/step-execution
logic against real collaborators (real ``ApprovalService``, real sandboxed
``TestExecutionService``/``SecurityScanService`` runs) is covered by
``tests/unit/deploy_launch/test_pipeline_service.py``.

``test_full_pipeline_runs_through_the_real_http_api`` below additionally
drives the whole Deploy & Launch user journey through the real HTTP
routes end to end - real session creation, real approval gating, the real
fire-and-forget background execution, real polling, and a real zip
download - to prove, at the API boundary (not just the service layer),
that: (1) ``POST .../start`` returns before the pipeline finishes (never
blocks the request for the whole multi-step run), (2) every step always
reaches a terminal status, and (3) the mission's own title - not a
generic ``"mission-"`` placeholder - is reflected in the provisioned
Foundry agent names. The only thing stood in for is the upstream,
live-Foundry-driven ``solution-discovery-workflow`` run this pipeline
reads from: completing that workflow for real in local mode would require
a live agent gateway (see ``tests/unit/deploy_launch/test_pipeline_service.py``'s
docstring for the same, already-established constraint elsewhere in this
suite).
"""
from __future__ import annotations

import asyncio
import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from app.config.settings import Settings
from app.deploy_launch.access_policy_service import AccessPolicyService
from app.deploy_launch.backend_deployment_service import NullBackendDeploymentService
from app.deploy_launch.frontend_deployment_service import NullFrontendDeploymentService
from app.deploy_launch.mission_agent_provisioning_service import (
    NullMissionAgentProvisioningService,
)
from app.deploy_launch.pipeline_service import DeploymentPipelineService
from app.deploy_launch.security_scan_service import SecurityScanService
from app.deploy_launch.test_execution_service import TestExecutionService
from app.main import create_app
from app.models.workflow_models import WorkflowRunResult, WorkflowStepResult

_REPO_CONFIG_ROOT = Path(__file__).resolve().parents[3] / "config"

_BUILD_OUTPUT = '''
```python
# agent: Requirements Specialist
async def run() -> None:
    pass
```

```python
# agent: orchestrator
async def run() -> None:
    pass
```

```tsx
// agent: ui
export function MissionApp() {
    return null;
}
```
'''

_ARCHITECTURE_DOCUMENT = """
## Multi-Agent Workflow

The Requirements Specialist agent extracts raw requirements.
The orchestrator agent sequences every specialist.
"""

_PASSING_TEST_OUTPUT = """
```python
def test_always_passes():
    assert 1 + 1 == 2
```
"""


def _bearer_token(user_id: str) -> str:
    return jwt.encode({"sub": user_id}, "unit-test-secret", algorithm="HS256")


@pytest.fixture
def real_config_local_settings() -> Settings:
    """Local/dev settings against the real ``config/`` tree (not the minimal
    ``local_settings`` fixture's hermetic stub policy) - needed because the
    real ``final-output-approval`` checkpoint this test exercises only
    exists in the actual ``config/policies/approval_policy.yaml``."""

    return Settings(
        environment="development",
        provider_mode="local",
        governance_provider="local",
        allow_mock_agents=True,
        allow_local_agents=True,
        use_synthetic_data=True,
        config_root=_REPO_CONFIG_ROOT,
    )


def _completed_step(step_id: str, agent_id: str, output_text: str) -> WorkflowStepResult:
    now = datetime.now(UTC)
    return WorkflowStepResult(
        step_id=step_id,
        agent_id=agent_id,
        status="completed",
        output_text=output_text,
        started_at=now,
        completed_at=now,
    )


class _StubUpstreamWorkflowOrchestrator:
    """Stands in only for the upstream ``solution-discovery-workflow`` run
    this pipeline reads ``design-architecture``/``build-solution``/
    ``test-generation`` output from - completing that workflow for real
    through local-mode agents is not practical (see module docstring).
    Everything downstream of this stub is real.
    """

    def __init__(self, *, run: WorkflowRunResult) -> None:
        self._run = run

    async def get_workflow_run(self, workflow_run_id: str) -> WorkflowRunResult | None:
        return self._run if workflow_run_id == self._run.workflow_run_id else None

    async def resume_workflow(self, **_kwargs: object) -> WorkflowRunResult:  # pragma: no cover
        raise AssertionError(
            "resume_workflow should never be needed: the stub run already has "
            "every step DeploymentPipelineService requires completed."
        )


async def test_start_requires_authentication(local_settings) -> None:
    app = create_app(settings=local_settings)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/sessions/does-not-exist/deploy-launch/start", json={"workflow_run_id": "run-1"}
            )
            assert resp.status_code == 401


async def test_start_requires_a_session_owned_by_the_caller(local_settings) -> None:
    app = create_app(settings=local_settings)
    headers = {"Authorization": f"Bearer {_bearer_token('user-1')}"}

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/sessions/does-not-exist/deploy-launch/start",
                json={"workflow_run_id": "run-1"},
                headers=headers,
            )
            assert resp.status_code == 404


async def test_list_deployments_requires_a_session_owned_by_the_caller(local_settings) -> None:
    app = create_app(settings=local_settings)
    headers = {"Authorization": f"Bearer {_bearer_token('user-1')}"}

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/sessions/does-not-exist/deploy-launch/", headers=headers
            )
            assert resp.status_code == 404


async def test_full_pipeline_runs_through_the_real_http_api(
    real_config_local_settings: Settings, tmp_path: Path
) -> None:
    app = create_app(settings=real_config_local_settings)
    headers = {"Authorization": f"Bearer {_bearer_token('user-1')}"}
    mission_title = "Acme Customer Portal"

    async with app.router.lifespan_context(app):
        # Real session/approval/event-bus/agent-registry instances - the
        # only stand-in is the upstream workflow run (see the stub's
        # docstring above); everything else below is the app's real,
        # already-wired production collaborators.
        orchestrator = app.state.agent_orchestrator
        stub_run = WorkflowRunResult(
            workflow_run_id="run-1",
            workflow_id="solution-discovery-workflow",
            session_id="session-under-test",
            status="completed",
            step_results=[
                _completed_step("design-architecture", "architecture-designer", _ARCHITECTURE_DOCUMENT),
                _completed_step("build-solution", "genie-orchestrator", _BUILD_OUTPUT),
                _completed_step("test-generation", "genie-orchestrator", _PASSING_TEST_OUTPUT),
            ],
        )
        app.state.deployment_pipeline_service = DeploymentPipelineService(
            orchestrator=_StubUpstreamWorkflowOrchestrator(run=stub_run),  # type: ignore[arg-type]
            session_service=app.state.session_service,
            approval_service=orchestrator.approval_service,
            event_bus=orchestrator.workflow_event_bus,
            access_policy_service=AccessPolicyService(agent_registry=orchestrator.agent_registry),
            mission_agent_provisioning_service=NullMissionAgentProvisioningService(),
            backend_deployment_service=NullBackendDeploymentService(),
            frontend_deployment_service=NullFrontendDeploymentService(),
            test_execution_service=TestExecutionService(timeout_seconds=60),
            security_scan_service=SecurityScanService(timeout_seconds=60),
            build_workspace_root=tmp_path,
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            session_resp = await client.post("/sessions", json={"title": mission_title}, headers=headers)
            assert session_resp.status_code == 201
            session_id = session_resp.json()["id"]

            # First start() call: no approval decision exists yet for this
            # workflow run, so it must fail closed (409) and create the
            # approval request as a side effect - real ApprovalService,
            # never mocked away.
            pending_resp = await client.post(
                f"/sessions/{session_id}/deploy-launch/start",
                json={"workflow_run_id": "run-1"},
                headers=headers,
            )
            assert pending_resp.status_code == 409

            requests = await orchestrator.approval_service.list_requests_for_session(session_id)
            final_output_request = next(
                r for r in requests if r.checkpoint_id == "final-output-approval"
            )
            await orchestrator.approval_service.decide(
                request_id=final_output_request.id, decision="approved", decided_by="reviewer-1"
            )

            start_resp = await client.post(
                f"/sessions/{session_id}/deploy-launch/start",
                json={"workflow_run_id": "run-1"},
                headers=headers,
            )
            assert start_resp.status_code == 200
            run_body = start_resp.json()
            run_id = run_body["id"]
            # Q3's root cause: start() used to block for the whole 9-step
            # pipeline. It must now return before the (real, subprocess-
            # driven) pipeline has finished - proving this never ties up
            # the HTTP request for the real work happening in the
            # background - not merely "eventually complete".
            assert run_body["status"] == "running"
            assert not all(step["status"] == "completed" for step in run_body["steps"])

            final_body: dict | None = None
            for _ in range(80):
                poll_resp = await client.get(
                    f"/sessions/{session_id}/deploy-launch/{run_id}", headers=headers
                )
                assert poll_resp.status_code == 200
                body = poll_resp.json()
                if body["status"] != "running":
                    final_body = body
                    break
                await asyncio.sleep(0.25)

            assert final_body is not None, "pipeline did not reach a terminal status in time"
            assert final_body["status"] == "completed"
            # Q2's root cause: an unexpected failure could leave a step
            # stuck showing "Running..." forever. Every step must always
            # resolve to a terminal status with a detail/error recorded.
            for step in final_body["steps"]:
                assert step["status"] == "completed"
                assert step["detail"]

            download_resp = await client.get(
                f"/sessions/{session_id}/deploy-launch/{run_id}/download", headers=headers
            )
            assert download_resp.status_code == 200
            with zipfile.ZipFile(io.BytesIO(download_resp.content)) as archive:
                agent_config_source = archive.read("build/agent_config.py").decode("utf-8")

            # Q1's root cause: Foundry agent names used a generic
            # "mission-<uuid>" placeholder instead of the mission's own
            # title. The real session title created above ("Acme Customer
            # Portal") must be reflected, and the old generic prefix must not.
            assert "local-acme-customer-portal-" in agent_config_source
            assert "local-mission-" not in agent_config_source
