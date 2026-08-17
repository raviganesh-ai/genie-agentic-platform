"""Unit tests for DeploymentPipelineService (full pipeline run + self-heal).

Uses Null Azure-calling collaborators (mission agent provisioning, backend
deployment, frontend deployment) so no real Azure credentials are needed,
but a REAL ``AccessPolicyService``, ``TestExecutionService`` (real
sandboxed pytest run) and ``SecurityScanService`` (real bandit run) - so
the step-execution logic itself is exercised for real, never mocked away.
Deploy & Launch has exactly one gate (the human clicking Start) - there is
no separate approval-checkpoint request/decide dance to exercise here.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from app.agents.models import AgentDefinition, AgentExecutionResult
from app.agents.registry import AgentRegistry
from app.deploy_launch.access_policy_service import AccessPolicyService
from app.deploy_launch.backend_deployment_service import (
    BackendDeploymentError,
    BackendDeploymentResult,
    NullBackendDeploymentService,
)
from app.deploy_launch.frontend_deployment_service import NullFrontendDeploymentService
from app.deploy_launch.mission_agent_provisioning_service import (
    NullMissionAgentProvisioningService,
)
from app.deploy_launch.mission_identity_service import NullMissionIdentityService
from app.deploy_launch.pipeline_service import DeploymentPipelineService
from app.deploy_launch.security_scan_service import SecurityScanService
from app.deploy_launch.test_execution_service import TestExecutionService
from app.models.workflow_models import WorkflowRunResult, WorkflowStepResult
from app.orchestration.workflow_event_bus import WorkflowEventBus

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

_REQUIREMENTS_OUTPUT = "The mission requires a search feature and an orchestrator agent."

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
        self._test_output_text = test_output_text
        self.execute_agent_calls: list[dict] = []
        self._run = WorkflowRunResult(
            workflow_run_id="run-1",
            workflow_id="solution-discovery-workflow",
            session_id="session-1",
            status="completed",
            step_results=[
                _completed_step("analyze-requirements", "genie-orchestrator", _REQUIREMENTS_OUTPUT),
                _completed_step("design-architecture", "architecture-designer", _ARCHITECTURE_DOCUMENT),
                _completed_step("build-solution", "genie-orchestrator", _BUILD_OUTPUT),
            ],
        )

    async def get_workflow_run(self, workflow_run_id: str) -> WorkflowRunResult | None:
        return self._run if workflow_run_id == "run-1" else None

    async def execute_agent(
        self,
        *,
        agent_id: str,
        prompt_id: str,
        variables: dict[str, str],
        session_id: str | None = None,
        trace_id: str | None = None,
    ) -> AgentExecutionResult:
        # Simulates Deploy & Launch's real, post-deploy call to the Test
        # Generation Agent (see pipeline_service.py's generate-test-suite
        # step) - never reads a pre-existing workflow step's output, since
        # test-generation no longer exists as a discovery-workflow step.
        self.execute_agent_calls.append({"agent_id": agent_id, "prompt_id": prompt_id, "variables": variables})
        return AgentExecutionResult(
            agent_id=agent_id, output_text=self._test_output_text, correlation_id="test-correlation-id"
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


class _FakeSharedMemory:
    """Minimal SharedMemoryStore.read double: returns a single record
    carrying {"output_text": ...} for each pre-seeded key, mirroring what
    orchestration_tools's _delegate really writes for a delegated step the
    instant its real generation finishes.
    """

    def __init__(self, records_by_key: dict[str, str]) -> None:
        self._records_by_key = records_by_key

    async def read(
        self, *, requesting_agent, session_id: str, trace_id: str, key: str | None = None
    ) -> list[SimpleNamespace]:
        if key is not None and key in self._records_by_key:
            return [SimpleNamespace(content={"output_text": self._records_by_key[key]})]
        return []


class _FakeOrchestratorPendingBuild:
    """Simulates a workflow run whose ``build-solution`` step has not
    completed yet - e.g. an earlier page's fire-and-forget kickoff never
    reached the server. Deploy & Launch's self-heal path
    (``_ensure_upstream_steps_completed``) must call ``resume_workflow``
    with a ``policies``/``excluded_agents`` override for ``build-solution``
    (not auto-derived by ``variable_sources`` in
    config/workflows/registry.yaml) so resuming doesn't hard-fail with
    ``PromptResolutionError: Prompt 'orchestrator-build-phase-v1' is
    missing required variable(s): [...]``.
    """

    def __init__(self, *, test_output_text: str) -> None:
        self.resume_calls: list[dict | None] = []
        self.execute_agent_calls: list[dict] = []
        self._test_output_text = test_output_text
        self._run = WorkflowRunResult(
            workflow_run_id="run-1",
            workflow_id="solution-discovery-workflow",
            session_id="session-1",
            status="running",
            step_results=[
                _completed_step("analyze-requirements", "genie-orchestrator", _REQUIREMENTS_OUTPUT),
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
            ],
        )
        return self._run

    async def execute_agent(
        self,
        *,
        agent_id: str,
        prompt_id: str,
        variables: dict[str, str],
        session_id: str | None = None,
        trace_id: str | None = None,
    ) -> AgentExecutionResult:
        self.execute_agent_calls.append({"agent_id": agent_id, "prompt_id": prompt_id, "variables": variables})
        return AgentExecutionResult(
            agent_id=agent_id, output_text=self._test_output_text, correlation_id="test-correlation-id"
        )


class _FakeOrchestratorStuckOnEarlierGate:
    """Simulates a run that is paused on an EARLIER stage's own
    ``requires_human_proceed`` gate (e.g. ``design-architecture``, which
    comes before ``build-solution``) - self-heal only ever targets
    build-solution, so resuming here legitimately cannot clear this
    earlier pause. ``resume_workflow`` returns normally (no exception),
    but build-solution is still not completed - the pipeline must fail
    closed immediately with a clear message rather than silently
    proceeding into a much later, unrelated step.
    """

    def __init__(self) -> None:
        self.resume_calls: list[dict | None] = []
        self._run = WorkflowRunResult(
            workflow_run_id="run-1",
            workflow_id="solution-discovery-workflow",
            session_id="session-1",
            status="waiting_for_proceed",
            detail="Waiting for the human to proceed to step 'design-architecture'.",
            step_results=[],
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
        return self._run


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
    return AccessPolicyService(
        agent_registry=registry,
        mission_identity_service=NullMissionIdentityService(),
    )


def _build_service(
    *, test_output_text: str, tmp_path: Path, backend_deployment_service=None
) -> DeploymentPipelineService:
    return DeploymentPipelineService(
        orchestrator=_FakeOrchestrator(test_output_text=test_output_text),  # type: ignore[arg-type]
        session_service=_FakeSessionService(),  # type: ignore[arg-type]
        event_bus=WorkflowEventBus(),
        access_policy_service=_access_policy_service(),
        mission_identity_service=NullMissionIdentityService(),
        mission_agent_provisioning_service=NullMissionAgentProvisioningService(),
        backend_deployment_service=backend_deployment_service or NullBackendDeploymentService(),
        frontend_deployment_service=NullFrontendDeploymentService(),
        test_execution_service=TestExecutionService(timeout_seconds=60),
        security_scan_service=SecurityScanService(timeout_seconds=60),
        build_workspace_root=tmp_path,
    )


async def test_full_pipeline_runs_every_step(tmp_path: Path):
    service = _build_service(test_output_text=_PASSING_TEST_OUTPUT, tmp_path=tmp_path)

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

    # Per-agent Foundry provisioning progress must be reported on the run
    # itself (not just an aggregate step status), each landing "completed"
    # with its own real foundry_agent_name once the pipeline finishes.
    assert {agent.agent_name for agent in run.provisioned_agents} == {
        "Requirements Specialist",
        "orchestrator",
    }
    assert all(agent.status == "completed" for agent in run.provisioned_agents)
    assert all(agent.foundry_agent_name for agent in run.provisioned_agents)

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

    # The deterministic frontend shell's live Agent Collaboration panel
    # reads window.__MISSION_AGENTS__ from runtime-config.js - it must list
    # every specialist by their real display name and must never include the
    # internal "orchestrator" coordinator as its own collaborator pill.
    runtime_config_source = (build_root.parent / "frontend" / "public" / "runtime-config.js").read_text(
        encoding="utf-8"
    )
    assert '__MISSION_AGENTS__ = ["Requirements Specialist"]' in runtime_config_source


async def test_start_self_heals_incomplete_build_solution_with_safe_policy_defaults(tmp_path: Path):
    """A run whose build-solution step never completed (e.g. a lost
    fire-and-forget kickoff) must self-heal via a resume_workflow call
    that explicitly overrides its unmapped ``policies``/``excluded_agents``
    variables with safe empty defaults - never omit them entirely (that
    used to hard-fail with a PromptResolutionError on every single "Retry
    Deploy & Launch" click, forever) and never re-trigger an
    already-completed step.
    """
    orchestrator = _FakeOrchestratorPendingBuild(test_output_text=_PASSING_TEST_OUTPUT)
    service = DeploymentPipelineService(
        orchestrator=orchestrator,  # type: ignore[arg-type]
        session_service=_FakeSessionService(),  # type: ignore[arg-type]
        event_bus=WorkflowEventBus(),
        access_policy_service=_access_policy_service(),
        mission_identity_service=NullMissionIdentityService(),
        mission_agent_provisioning_service=NullMissionAgentProvisioningService(),
        backend_deployment_service=NullBackendDeploymentService(),
        frontend_deployment_service=NullFrontendDeploymentService(),
        test_execution_service=TestExecutionService(timeout_seconds=60),
        security_scan_service=SecurityScanService(timeout_seconds=60),
        build_workspace_root=tmp_path,
    )

    # First call: build-solution isn't complete yet, so the self-heal path
    # resumes it (with the safe overrides) BEFORE the pipeline steps run.
    run = await service.start(session_id="session-1", requesting_user_id="user-1", workflow_run_id="run-1")
    run = await service.wait_for_run(run.id)

    assert len(orchestrator.resume_calls) == 1
    step_inputs = orchestrator.resume_calls[0]
    assert step_inputs is not None
    assert set(step_inputs) == {"build-solution"}
    assert step_inputs["build-solution"].variables == {"policies": "", "excluded_agents": ""}
    assert run.status == "completed"

    # A second start() call for a NEW pipeline run: build-solution is now
    # complete (the fake applied it during the first resume), so no
    # further resume call is made - an already-completed build must never
    # be re-triggered.
    run2 = await service.start(session_id="session-1", requesting_user_id="user-1", workflow_run_id="run-1")
    run2 = await service.wait_for_run(run2.id)

    assert run2.status == "completed"
    assert len(orchestrator.resume_calls) == 1


async def test_start_uses_shared_memory_output_without_waiting_for_official_step_completion(
    tmp_path: Path,
):
    """``build-solution``'s real output can already be sitting in Shared
    Collaboration Memory (written by the delegation tool - see
    ``app.agents.tools.orchestration_tools``'s ``_delegate`` - the instant
    the real generation finishes) well before genie-orchestrator's own
    separate, slower echo-completion turn ever marks the workflow step's
    own ``WorkflowStepResult`` as ``"completed"``. Once the human can see
    (and has approved) that real generated code on Workshop, Deploy &
    Launch must not impose any additional backend wait of its own for the
    same content to be echoed back a second time - so no ``resume_workflow``
    self-heal call should happen at all here, and the real memory-sourced
    text must be what downstream steps (provision-foundry-agents,
    generate-test-suite) actually use.
    """
    orchestrator = _FakeOrchestratorPendingBuild(test_output_text=_PASSING_TEST_OUTPUT)
    orchestrator.agent_registry = AgentRegistry(
        {
            "genie-orchestrator": AgentDefinition(
                id="genie-orchestrator",
                name="Genie Orchestrator",
                role="mission_orchestration",
                description="Drives each mission phase.",
                allowed_tools=[],
                memory_access=["shared"],
                enabled=True,
            )
        }
    )
    orchestrator.memory_service = SimpleNamespace(
        shared=_FakeSharedMemory({"build-solution": _BUILD_OUTPUT})
    )

    service = DeploymentPipelineService(
        orchestrator=orchestrator,  # type: ignore[arg-type]
        session_service=_FakeSessionService(),  # type: ignore[arg-type]
        event_bus=WorkflowEventBus(),
        access_policy_service=_access_policy_service(),
        mission_identity_service=NullMissionIdentityService(),
        mission_agent_provisioning_service=NullMissionAgentProvisioningService(),
        backend_deployment_service=NullBackendDeploymentService(),
        frontend_deployment_service=NullFrontendDeploymentService(),
        test_execution_service=TestExecutionService(timeout_seconds=60),
        security_scan_service=SecurityScanService(timeout_seconds=60),
        build_workspace_root=tmp_path,
    )

    run = await service.start(session_id="session-1", requesting_user_id="user-1", workflow_run_id="run-1")
    run = await service.wait_for_run(run.id)

    assert orchestrator.resume_calls == []
    assert run.status == "completed"
    test_generation_call = orchestrator.execute_agent_calls[-1]
    assert test_generation_call["agent_id"] == "test-generation-agent"
    assert test_generation_call["variables"]["artifact"] == _BUILD_OUTPUT


async def test_start_fails_closed_with_actionable_error_when_stuck_on_earlier_gate(tmp_path: Path):
    """If the self-heal resume returns without clearing build-solution
    (e.g. the run is genuinely paused on an EARLIER stage's own proceed
    gate), the pipeline must fail immediately with a clear, actionable
    message attributed to the first step - never silently proceed into
    `_execute_steps` and blow up on a much later, unrelated step with a
    confusing "Workflow step 'build-solution' has not completed" error.
    """
    orchestrator = _FakeOrchestratorStuckOnEarlierGate()
    service = DeploymentPipelineService(
        orchestrator=orchestrator,  # type: ignore[arg-type]
        session_service=_FakeSessionService(),  # type: ignore[arg-type]
        event_bus=WorkflowEventBus(),
        access_policy_service=_access_policy_service(),
        mission_identity_service=NullMissionIdentityService(),
        mission_agent_provisioning_service=NullMissionAgentProvisioningService(),
        backend_deployment_service=NullBackendDeploymentService(),
        frontend_deployment_service=NullFrontendDeploymentService(),
        test_execution_service=TestExecutionService(timeout_seconds=60),
        security_scan_service=SecurityScanService(timeout_seconds=60),
        build_workspace_root=tmp_path,
    )

    run = await service.start(session_id="session-1", requesting_user_id="user-1", workflow_run_id="run-1")
    run = await service.wait_for_run(run.id)

    assert len(orchestrator.resume_calls) == 1
    assert run.status == "failed"
    first_step = run.steps[0]
    assert first_step.status == "failed"
    assert first_step.error is not None
    assert "not fully complete" in first_step.error
    assert "design-architecture" in first_step.error
    # Every OTHER step must never have been touched - the failure must be
    # attributed to the very first step, not a later, unrelated one.
    assert all(step.status == "pending" for step in run.steps[1:])


async def test_pipeline_fails_closed_when_generated_tests_fail(tmp_path: Path):
    service = _build_service(test_output_text=_FAILING_TEST_OUTPUT, tmp_path=tmp_path)

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


async def test_start_returns_a_visible_running_run_before_any_slow_lookup_happens(tmp_path: Path):
    """start() must create and store the run (status "running", every step
    "pending") BEFORE resolving the session/workflow run - so a poller
    calling list_runs_for_session immediately after start() returns always
    sees a real run, never an empty list while slow prep work is still
    silently happening in the background."""
    service = _build_service(test_output_text=_PASSING_TEST_OUTPUT, tmp_path=tmp_path)

    run = await service.start(session_id="session-1", requesting_user_id="user-1", workflow_run_id="run-1")

    assert run.status == "running"
    assert all(step.status == "pending" for step in run.steps)
    assert service.list_runs_for_session("session-1") == [run]

    await service.wait_for_run(run.id)


async def test_start_fails_the_run_visibly_when_the_workflow_run_is_unknown(tmp_path: Path):
    """An unknown workflow_run_id must never raise synchronously out of
    start() (there would be no caller left to catch it once steps are
    running in the background) - it must resolve the already-visible run to
    "failed" with the error attributed to the first step, so the failure
    shows up in the same per-step list the user is already watching."""
    service = _build_service(test_output_text=_PASSING_TEST_OUTPUT, tmp_path=tmp_path)

    run = await service.start(
        session_id="session-1", requesting_user_id="user-1", workflow_run_id="does-not-exist"
    )
    run = await service.wait_for_run(run.id)

    assert run.status == "failed"
    assert run.steps[0].status == "failed"
    assert run.steps[0].error is not None


class _FailOnceThenSucceedBackendDeploymentService:
    """Fails the very first ``deploy()`` call (simulating a real transient
    Azure failure - e.g. the ACR build dependency-conflict bug this test
    guards against) and succeeds on every subsequent call, so a test can
    assert that retrying actually reaches a real "completed" state rather
    than failing again for an unrelated reason."""

    def __init__(self) -> None:
        self.call_count = 0

    async def deploy(
        self,
        *,
        mission_slug: str,
        build_root: Path,
        mission_identity_resource_id: str | None = None,
        on_progress=None,
    ) -> BackendDeploymentResult:
        self.call_count += 1
        if self.call_count == 1:
            raise BackendDeploymentError("Simulated ACR build failure.")
        return BackendDeploymentResult(
            image_tag=f"local/{mission_slug}:dev",
            backend_url=f"http://localhost/missions/{mission_slug}/backend",
        )


async def test_retry_from_a_failed_step_reuses_the_same_run_and_its_prior_artifacts(tmp_path: Path):
    """Regression test: retrying (resume_from_step set) a run that already
    failed partway through must continue the SAME pipeline_run - not mint a
    brand-new one - so later steps can still read the artifacts
    (materialized build, provisioned Foundry agent names) produced by the
    EARLIER, already-completed steps of that same run. Before this fix,
    start() always created a fresh pipeline_run.id regardless of
    resume_from_step, which orphaned that prior work and made every retry
    fail again."""
    backend_deployment_service = _FailOnceThenSucceedBackendDeploymentService()
    service = _build_service(
        test_output_text=_PASSING_TEST_OUTPUT,
        tmp_path=tmp_path,
        backend_deployment_service=backend_deployment_service,
    )

    first_attempt = await service.start(
        session_id="session-1", requesting_user_id="user-1", workflow_run_id="run-1"
    )
    first_attempt = await service.wait_for_run(first_attempt.id)

    assert first_attempt.status == "failed"
    failed_step = next(step for step in first_attempt.steps if step.status == "failed")
    assert failed_step.step_id == "deploy-backend-service"
    # provision-foundry-agents (the step immediately before the failure)
    # must have actually completed on this first attempt.
    provision_step = next(
        step for step in first_attempt.steps if step.step_id == "provision-foundry-agents"
    )
    assert provision_step.status == "completed"

    retried = await service.start(
        session_id="session-1",
        requesting_user_id="user-1",
        workflow_run_id="run-1",
        resume_from_step=failed_step.step_id,
    )

    # The retry must be the SAME run (same id) - a new pipeline_run.id would
    # mean the provision-foundry-agents artifacts (materialized build,
    # Foundry agent names) from the first attempt are unreachable.
    assert retried.id == first_attempt.id

    retried = await service.wait_for_run(retried.id)

    assert retried.status == "completed"
    assert all(step.status == "completed" for step in retried.steps)
    assert backend_deployment_service.call_count == 2
    # Only one run should ever be visible for this session - a retry must
    # not leave a stale duplicate "failed" entry alongside a new run.
    assert [run.id for run in service.list_runs_for_session("session-1")] == [first_attempt.id]

