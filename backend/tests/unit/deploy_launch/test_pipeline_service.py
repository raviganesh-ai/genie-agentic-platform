"""Unit tests for DeploymentPipelineService (approval gating + full pipeline run).

Uses Null Azure-calling collaborators (mission agent provisioning, backend
deployment, frontend deployment) so no real Azure credentials are needed,
but a REAL ``AccessPolicyService``, ``TestExecutionService`` (real
sandboxed pytest run) and ``SecurityScanService`` (real bandit run), and a
real, in-memory ``ApprovalService`` - so the approval-gating and
step-execution logic itself is exercised for real, never mocked away.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agents.models import AgentDefinition
from app.agents.registry import AgentRegistry
from app.deploy_launch.access_policy_service import AccessPolicyService
from app.deploy_launch.backend_deployment_service import NullBackendDeploymentService
from app.deploy_launch.frontend_deployment_service import NullFrontendDeploymentService
from app.deploy_launch.mission_agent_provisioning_service import (
    NullMissionAgentProvisioningService,
)
from app.deploy_launch.pipeline_service import (
    DeploymentApprovalPendingError,
    DeploymentPipelineService,
)
from app.deploy_launch.security_scan_service import SecurityScanService
from app.deploy_launch.test_execution_service import TestExecutionService
from app.governance.approval_service import ApprovalPolicyDocument, ApprovalService
from app.models.approval_models import ApprovalCheckpoint
from app.models.workflow_models import WorkflowRunResult, WorkflowStepResult
from app.orchestration.workflow_event_bus import WorkflowEventBus
from app.repositories.approval_repository import InMemoryApprovalRepository

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

_FAILING_TEST_OUTPUT = """
```python
def test_always_fails():
    assert 1 == 2
```
"""


class _FakeSessionService:
    async def get_session(self, *, session_id: str, requesting_user_id: str) -> SimpleNamespace:
        return SimpleNamespace(title="Acme Mission")


class _FakeOrchestrator:
    def __init__(self, *, test_output_text: str) -> None:
        self._run = WorkflowRunResult(
            workflow_run_id="run-1",
            workflow_id="solution-discovery-workflow",
            session_id="session-1",
            status="completed",
            step_results=[
                _completed_step("design-architecture", "architecture-designer", _ARCHITECTURE_DOCUMENT),
                _completed_step("build-solution", "genie-orchestrator", _BUILD_OUTPUT),
                _completed_step("test-generation", "genie-orchestrator", test_output_text),
            ],
        )

    async def get_workflow_run(self, workflow_run_id: str) -> WorkflowRunResult | None:
        return self._run if workflow_run_id == "run-1" else None


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


class _FakeOrchestratorPendingBuild:
    """Simulates a workflow run whose ``build-solution`` AND ``peer-review``
    steps have not completed yet - e.g. an earlier page's fire-and-forget
    kickoff never reached the server. Deploy & Launch's self-heal path
    (``_ensure_upstream_steps_completed``) must call ``resume_workflow``
    with a ``policies``/``excluded_agents`` override for ``build-solution``
    and a ``policies`` override for ``peer-review`` (none of these are
    auto-derived by ``variable_sources`` in config/workflows/registry.yaml)
    so resuming doesn't hard-fail with ``PromptResolutionError: Prompt
    'orchestrator-build-phase-v1'/'orchestrator-peer-review-phase-v1' is
    missing required variable(s): [...]``.
    """

    def __init__(self, *, test_output_text: str) -> None:
        self.resume_calls: list[dict | None] = []
        self._test_output_text = test_output_text
        self._run = WorkflowRunResult(
            workflow_run_id="run-1",
            workflow_id="solution-discovery-workflow",
            session_id="session-1",
            status="running",
            step_results=[
                _completed_step("design-architecture", "architecture-designer", _ARCHITECTURE_DOCUMENT),
            ],
        )

    async def get_workflow_run(self, workflow_run_id: str) -> WorkflowRunResult | None:
        return self._run if workflow_run_id == "run-1" else None

    async def resume_workflow(
        self,
        *,
        workflow_run_id: str,
        session_id: str,
        trace_id: str | None = None,
        step_inputs: dict | None = None,
        transcript_text: str = "",
    ) -> WorkflowRunResult:
        self.resume_calls.append(step_inputs)
        self._run = WorkflowRunResult(
            workflow_run_id="run-1",
            workflow_id="solution-discovery-workflow",
            session_id="session-1",
            status="completed",
            step_results=[
                *self._run.step_results,
                _completed_step("build-solution", "genie-orchestrator", _BUILD_OUTPUT),
                _completed_step("test-generation", "genie-orchestrator", self._test_output_text),
                _completed_step("peer-review", "genie-orchestrator", "Peer review verdict: approved."),
            ],
        )
        return self._run


def _approval_service() -> ApprovalService:
    policy = ApprovalPolicyDocument(
        default_expiry_minutes=1440,
        checkpoints=[
            ApprovalCheckpoint(
                id="final-output-approval",
                name="Final Output Approval",
                description="Approve the mission for deployment.",
            )
        ],
    )
    return ApprovalService(repository=InMemoryApprovalRepository(), policy=policy)


def _access_policy_service() -> AccessPolicyService:
    agent = AgentDefinition(
        id="requirements-analyst",
        name="Requirements Analyst",
        role="analysis",
        description="Analyzes requirements.",
        allowed_tools=["azure_ai_search"],
        memory_access=["shared"],
        enabled=True,
    )
    registry = AgentRegistry({agent.id: agent})
    return AccessPolicyService(agent_registry=registry)


def _build_service(
    *, test_output_text: str, tmp_path: Path
) -> tuple[DeploymentPipelineService, ApprovalService]:
    approval_service = _approval_service()
    service = DeploymentPipelineService(
        orchestrator=_FakeOrchestrator(test_output_text=test_output_text),  # type: ignore[arg-type]
        session_service=_FakeSessionService(),  # type: ignore[arg-type]
        approval_service=approval_service,
        event_bus=WorkflowEventBus(),
        access_policy_service=_access_policy_service(),
        mission_agent_provisioning_service=NullMissionAgentProvisioningService(),
        backend_deployment_service=NullBackendDeploymentService(),
        frontend_deployment_service=NullFrontendDeploymentService(),
        test_execution_service=TestExecutionService(timeout_seconds=60),
        security_scan_service=SecurityScanService(timeout_seconds=60),
        build_workspace_root=tmp_path,
    )
    return service, approval_service


async def test_start_requests_approval_and_raises_pending_when_none_exists(tmp_path: Path):
    service, approval_service = _build_service(test_output_text=_PASSING_TEST_OUTPUT, tmp_path=tmp_path)

    with pytest.raises(DeploymentApprovalPendingError):
        await service.start(session_id="session-1", requesting_user_id="user-1", workflow_run_id="run-1")

    requests = await approval_service.list_requests_for_session("session-1")
    assert len(requests) == 1
    assert requests[0].checkpoint_id == "final-output-approval"
    assert requests[0].subject_id == "run-1"


async def test_start_raises_pending_again_while_request_still_pending(tmp_path: Path):
    service, _ = _build_service(test_output_text=_PASSING_TEST_OUTPUT, tmp_path=tmp_path)

    with pytest.raises(DeploymentApprovalPendingError):
        await service.start(session_id="session-1", requesting_user_id="user-1", workflow_run_id="run-1")

    with pytest.raises(DeploymentApprovalPendingError):
        await service.start(session_id="session-1", requesting_user_id="user-1", workflow_run_id="run-1")


async def test_full_pipeline_runs_every_step_once_approved(tmp_path: Path):
    service, approval_service = _build_service(test_output_text=_PASSING_TEST_OUTPUT, tmp_path=tmp_path)

    with pytest.raises(DeploymentApprovalPendingError):
        await service.start(session_id="session-1", requesting_user_id="user-1", workflow_run_id="run-1")

    requests = await approval_service.list_requests_for_session("session-1")
    await approval_service.decide(request_id=requests[0].id, decision="approved", decided_by="reviewer-1")

    # start() returns as soon as the run is created (status "running") - the
    # nine steps execute in a background task, exactly like Architecture
    # Studio's build-solution kickoff - so tests must await that background
    # work to finish rather than expecting the steps to have already run by
    # the time start() itself returns.
    run = await service.start(session_id="session-1", requesting_user_id="user-1", workflow_run_id="run-1")
    run = await service.wait_for_run(run.id)

    assert run.status == "completed"
    assert all(step.status == "completed" for step in run.steps)
    assert run.access_policy is not None
    assert run.backend_url is not None
    assert run.frontend_url is not None
    assert run.launch_url == run.frontend_url
    assert run.test_summary is not None
    assert run.security_findings_count is not None

    # The full specialist-name -> Foundry-name mapping (not just the
    # orchestrator's own name) must be materialized as a real agent_config.py
    # file so the generated orchestrator.py's call_<agent> tools have a real,
    # non-hardcoded source of truth for each specialist's Foundry agent name.
    build_root = service.get_build_root(run.id)
    assert build_root is not None
    agent_config_source = (build_root / "agent_config.py").read_text(encoding="utf-8")
    assert "AGENT_FOUNDRY_NAMES" in agent_config_source
    assert "Requirements Specialist" in agent_config_source
    # The mission's own title ("Acme Mission") - not a generic "mission-<uuid>"
    # string - must be reflected in the provisioned Foundry agent names.
    assert "local-acme-mission-" in agent_config_source


async def test_start_self_heals_incomplete_build_solution_with_safe_policy_defaults(tmp_path: Path):
    """A run whose build-solution AND peer-review steps never completed
    (e.g. a lost fire-and-forget kickoff) must self-heal via a
    resume_workflow call that explicitly overrides both steps' unmapped
    ``policies``/``excluded_agents`` variables with safe empty defaults -
    never omit them entirely (that used to hard-fail with a
    PromptResolutionError on every single "Retry Deploy & Launch" click,
    forever) and never re-trigger an already-completed step.
    """
    orchestrator = _FakeOrchestratorPendingBuild(test_output_text=_PASSING_TEST_OUTPUT)
    approval_service = _approval_service()
    service = DeploymentPipelineService(
        orchestrator=orchestrator,  # type: ignore[arg-type]
        session_service=_FakeSessionService(),  # type: ignore[arg-type]
        approval_service=approval_service,
        event_bus=WorkflowEventBus(),
        access_policy_service=_access_policy_service(),
        mission_agent_provisioning_service=NullMissionAgentProvisioningService(),
        backend_deployment_service=NullBackendDeploymentService(),
        frontend_deployment_service=NullFrontendDeploymentService(),
        test_execution_service=TestExecutionService(timeout_seconds=60),
        security_scan_service=SecurityScanService(timeout_seconds=60),
        build_workspace_root=tmp_path,
    )

    # First call: build-solution isn't complete yet, so the self-heal path
    # resumes it (with the safe overrides) BEFORE the approval check even
    # runs - then still raises pending since no approval decision exists yet.
    with pytest.raises(DeploymentApprovalPendingError):
        await service.start(session_id="session-1", requesting_user_id="user-1", workflow_run_id="run-1")

    assert len(orchestrator.resume_calls) == 1
    step_inputs = orchestrator.resume_calls[0]
    assert step_inputs is not None
    # peer-review has no approval checkpoint gating its wave, so the same
    # resume_workflow call can reach it too (once build-review-approval is
    # granted) - its own unmapped `policies` variable must ALSO be safely
    # overridden here, not just build-solution's.
    assert set(step_inputs) == {"build-solution", "peer-review"}
    assert step_inputs["build-solution"].variables == {"policies": "", "excluded_agents": ""}
    assert step_inputs["peer-review"].variables == {"policies": ""}

    requests = await approval_service.list_requests_for_session("session-1")
    await approval_service.decide(request_id=requests[0].id, decision="approved", decided_by="reviewer-1")

    # Second call: build-solution/test-generation are now both complete
    # (the fake applied them during the first resume), so no further resume
    # call is made - an already-completed build must never be re-triggered.
    run = await service.start(session_id="session-1", requesting_user_id="user-1", workflow_run_id="run-1")
    run = await service.wait_for_run(run.id)

    assert run.status == "completed"
    assert len(orchestrator.resume_calls) == 1


async def test_pipeline_fails_closed_when_generated_tests_fail(tmp_path: Path):
    service, approval_service = _build_service(test_output_text=_FAILING_TEST_OUTPUT, tmp_path=tmp_path)

    with pytest.raises(DeploymentApprovalPendingError):
        await service.start(session_id="session-1", requesting_user_id="user-1", workflow_run_id="run-1")

    requests = await approval_service.list_requests_for_session("session-1")
    await approval_service.decide(request_id=requests[0].id, decision="approved", decided_by="reviewer-1")

    # The step failure happens in the background task, so it surfaces as a
    # "failed" run/step status once awaited - never as an exception raised
    # out of start() itself (there is no synchronous caller left to catch
    # it once the pipeline is running in the background).
    run = await service.start(session_id="session-1", requesting_user_id="user-1", workflow_run_id="run-1")
    run = await service.wait_for_run(run.id)

    assert run.status == "failed"
    failed_step = next(step for step in run.steps if step.step_id == "execute-test-suite")
    assert failed_step.status == "failed"
    assert failed_step.error is not None
