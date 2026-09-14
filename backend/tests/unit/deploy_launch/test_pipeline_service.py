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

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

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
from app.deploy_launch.models import DeploymentPipelineRun, DeploymentStepResult
from app.deploy_launch.pipeline_service import (
    DeploymentPipelineService,
    DeploymentPipelineStepFailedError,
)
from app.deploy_launch.security_scan_service import (
    SecurityFinding,
    SecurityScanResult,
    SecurityScanService,
)
from app.deploy_launch.test_execution_service import TestExecutionService
from app.models.workflow_models import WorkflowRunResult, WorkflowStepInput, WorkflowStepResult
from app.orchestration.workflow_event_bus import WorkflowEventBus
from app.repositories.deployment_run_repository import InMemoryDeploymentRunRepository

_BUILD_OUTPUT = '''
```python
# agent: Requirements Specialist
async def run() -> None:
    pass
```

```python
# agent: orchestrator
class OrchestratorAgent:
    async def run(self, ui_message: str) -> None:
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

_REQUIREMENTS_OUTPUT = "[REQ-001] The mission requires a search feature and an orchestrator agent."

_PASSING_TEST_OUTPUT = """
```python
# REQ-001
def test_req_001_always_passes():
    assert 1 + 1 == 2
```
"""

_FAILING_TEST_OUTPUT = """
```python
# REQ-001
def test_req_001_always_fails():
    assert 1 == 2
```
"""


class _FakeSessionService:
    async def get_session(self, *, session_id: str, requesting_user_id: str) -> SimpleNamespace:
        return SimpleNamespace(title="Acme Mission")


class _FakeOrchestrator:
    def __init__(
        self,
        *,
        test_output_text: str,
        requirements_output: str = _REQUIREMENTS_OUTPUT,
        agent_scope_id: str | None = None,
    ) -> None:
        self._test_output_text = test_output_text
        self.execute_agent_calls: list[dict] = []
        self._run = WorkflowRunResult(
            workflow_run_id="run-1",
            workflow_id="solution-discovery-workflow",
            session_id="session-1",
            status="completed",
            agent_scope_id=agent_scope_id,
            step_results=[
                _completed_step("analyze-requirements", "genie-orchestrator", requirements_output),
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
        # Simulates Deploy & Launch's real, pre-deploy call to the Test
        # Generation Agent (see pipeline_service.py's generate-test-suite
        # step) - never reads a pre-existing workflow step's output, since
        # test-generation no longer exists as a discovery-workflow step.
        self.execute_agent_calls.append({"agent_id": agent_id, "prompt_id": prompt_id, "variables": variables})
        return AgentExecutionResult(
            agent_id=agent_id, output_text=self._test_output_text, correlation_id="test-correlation-id"
        )


class _RepairingFakeOrchestrator(_FakeOrchestrator):
    def __init__(self, *, test_outputs: list[str], requirements_output: str) -> None:
        super().__init__(
            test_output_text=test_outputs[-1],
            requirements_output=requirements_output,
        )
        self._test_outputs = list(test_outputs)
        self.resume_calls: list[dict[str, WorkflowStepInput]] = []

    async def execute_agent(
        self,
        *,
        agent_id: str,
        prompt_id: str,
        variables: dict[str, str],
        session_id: str | None = None,
        trace_id: str | None = None,
    ) -> AgentExecutionResult:
        self.execute_agent_calls.append(
            {"agent_id": agent_id, "prompt_id": prompt_id, "variables": variables}
        )
        output = self._test_outputs.pop(0) if self._test_outputs else self._test_output_text
        return AgentExecutionResult(
            agent_id=agent_id,
            output_text=output,
            correlation_id="test-correlation-id",
        )

    async def resume_workflow(
        self,
        *,
        workflow_run_id: str,
        session_id: str,
        trace_id: str,
        step_inputs: dict[str, WorkflowStepInput],
    ) -> WorkflowRunResult:
        self.resume_calls.append(step_inputs)
        return self._run


class _BuildValidationRepairingFakeOrchestrator(_RepairingFakeOrchestrator):
    def __init__(self) -> None:
        super().__init__(
            test_outputs=[_PASSING_TEST_OUTPUT],
            requirements_output=_REQUIREMENTS_OUTPUT,
        )
        invalid_build = _BUILD_OUTPUT.replace(
            "export function MissionApp() {",
            'export function MissionApp() {\n    const invalid = file.name !== "fixed.json";',
        )
        self._run.step_results[-1] = _completed_step(
            "build-solution", "genie-orchestrator", invalid_build
        )

    async def resume_workflow(
        self,
        *,
        workflow_run_id: str,
        session_id: str,
        trace_id: str,
        step_inputs: dict[str, WorkflowStepInput],
    ) -> WorkflowRunResult:
        self.resume_calls.append(step_inputs)
        self._run.step_results[-1] = _completed_step(
            "build-solution", "genie-orchestrator", _BUILD_OUTPUT
        )
        return self._run


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
        self.writes: list[dict] = []

    async def read(
        self, *, requesting_agent, session_id: str, trace_id: str, key: str | None = None
    ) -> list[SimpleNamespace]:
        if key is not None and key in self._records_by_key:
            return [SimpleNamespace(content={"output_text": self._records_by_key[key]})]
        return []

    async def write(self, **kwargs) -> SimpleNamespace:
        self.writes.append(kwargs)
        self._records_by_key[kwargs["key"]] = kwargs["content"]["output_text"]
        return SimpleNamespace(content=kwargs["content"])


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
    *,
    test_output_text: str,
    tmp_path: Path,
    backend_deployment_service=None,
    requirements_output: str = _REQUIREMENTS_OUTPUT,
    run_repository=None,
    prototype_max_active_per_owner: int = 3,
) -> DeploymentPipelineService:
    return DeploymentPipelineService(
        orchestrator=_FakeOrchestrator(
            test_output_text=test_output_text,
            requirements_output=requirements_output,
        ),  # type: ignore[arg-type]
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
        run_repository=run_repository,
        prototype_max_active_per_owner=prototype_max_active_per_owner,
    )


async def test_discovery_build_run_supplies_deploy_requirements_and_architecture(
    tmp_path: Path,
) -> None:
    service = _build_service(test_output_text=_PASSING_TEST_OUTPUT, tmp_path=tmp_path)
    run = WorkflowRunResult(
        workflow_run_id="discovery-run-1",
        workflow_id="discovery-build-workflow",
        session_id="session-1",
        status="completed",
        step_results=[
            WorkflowStepResult(
                step_id="build-solution",
                agent_id="genie-orchestrator",
                status="completed",
                output_text=_BUILD_OUTPUT,
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
                resolved_variables={
                    "requirements": _REQUIREMENTS_OUTPUT,
                    "architecture": _ARCHITECTURE_DOCUMENT,
                },
            )
        ],
    )

    requirements = await service._get_approved_requirements(run, trace_id="trace-1")
    architecture = await service._get_approved_architecture(run, trace_id="trace-1")

    assert requirements == _REQUIREMENTS_OUTPUT
    assert architecture == _ARCHITECTURE_DOCUMENT


async def test_full_pipeline_runs_every_step(tmp_path: Path):
    service = _build_service(test_output_text=_PASSING_TEST_OUTPUT, tmp_path=tmp_path)

    # start() returns as soon as the run is created (status "running") - the
    # Eight steps execute in a background task, exactly like Architecture
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
    assert run.security_findings_count is None

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


class _FakeProtectedBackendDeploymentService:
    def __init__(self, delete_events: list[str] | None = None) -> None:
        self.frontend_origin: str | None = None
        self.delete_events = delete_events

    async def deploy(self, *, mission_slug: str, **kwargs):
        return BackendDeploymentResult(
            image_tag=f"acr/{mission_slug}:dev",
            backend_url=f"https://{mission_slug}-backend.example.com",
        )

    async def configure_gateway_frontend_origin(
        self,
        *,
        mission_slug: str,
        frontend_origin: str,
    ):
        del mission_slug
        self.frontend_origin = frontend_origin

    async def delete(self, *, mission_slug: str, app_name: str | None = None):
        del app_name
        if self.delete_events is not None:
            self.delete_events.append(f"backend:{mission_slug}")


class _FakeProtectedFrontendDeploymentService:
    def __init__(self, delete_events: list[str] | None = None) -> None:
        self.mission_identity_resource_id: str | None = None
        self.delete_events = delete_events

    async def deploy(self, *, mission_identity_resource_id=None, **kwargs):
        self.mission_identity_resource_id = mission_identity_resource_id
        return SimpleNamespace(frontend_url="https://prototype.example.com")

    async def delete(self, *, mission_slug: str, app_name: str | None = None):
        del app_name
        if self.delete_events is not None:
            self.delete_events.append(f"frontend:{mission_slug}")


async def test_prototype_pipeline_uses_anonymous_apim_without_entra(tmp_path: Path):
    test_output = """
```python
# REQ-001
import httpx
import os

def test_req_001_uses_public_gateway():
    invoke_url = os.environ["MISSION_BACKEND_URL"] + "/invoke"
    def invoke():
        return httpx.post(invoke_url, json={"message": "{}", "attachments": []})
    assert invoke_url.startswith("https://")
    assert callable(invoke)
    assert "MISSION_ACCESS_TOKEN" not in os.environ
    assert "MISSION_ACCEPTANCE_TEST_KEY" not in os.environ
```
"""
    backend_service = _FakeProtectedBackendDeploymentService()
    frontend_service = _FakeProtectedFrontendDeploymentService()
    service = DeploymentPipelineService(
        orchestrator=_FakeOrchestrator(test_output_text=test_output),  # type: ignore[arg-type]
        session_service=_FakeSessionService(),  # type: ignore[arg-type]
        event_bus=WorkflowEventBus(),
        access_policy_service=_access_policy_service(),
        mission_identity_service=NullMissionIdentityService(),
        mission_agent_provisioning_service=NullMissionAgentProvisioningService(),
        backend_deployment_service=backend_service,  # type: ignore[arg-type]
        frontend_deployment_service=frontend_service,  # type: ignore[arg-type]
        test_execution_service=TestExecutionService(timeout_seconds=60),
        security_scan_service=SecurityScanService(timeout_seconds=60),
        build_workspace_root=tmp_path,
    )

    run = await service.start(
        session_id="session-1", requesting_user_id="user-1", workflow_run_id="run-1"
    )
    run = await service.wait_for_run(run.id)

    assert run.status == "completed"
    assert backend_service.frontend_origin == "https://prototype.example.com"
    assert frontend_service.mission_identity_resource_id is not None
    assert "userAssignedIdentities" in frontend_service.mission_identity_resource_id
    runtime_config = (
        tmp_path / run.id / "frontend" / "public" / "runtime-config.js"
    ).read_text(encoding="utf-8")
    assert "__MISSION_ENTRA_" not in runtime_config
    generated_main = (tmp_path / run.id / "backend" / "main.py").read_text(
        encoding="utf-8"
    )
    assert "authenticate_request" not in generated_main


class _BlockingSecurityScanService:
    def __init__(self) -> None:
        self.calls = 0

    async def scan(self, *, build_root: Path) -> SecurityScanResult:
        del build_root
        self.calls += 1
        return SecurityScanResult(
            ran=True,
            findings=[
                SecurityFinding(
                    file="generated.py",
                    severity="high",
                    description="simulated blocking finding",
                )
            ],
            summary="simulated blocking finding",
        )


async def test_passing_fidelity_launches_without_running_security_scan(tmp_path: Path):
    test_output = '''
```python
# REQ-001
import httpx
import os

def test_req_001_uses_live_prototype():
    invoke_url = os.environ["MISSION_BACKEND_URL"] + "/invoke"
    def invoke():
        return httpx.post(invoke_url, json={"message": "{}", "attachments": []})
    assert invoke_url.startswith("https://")
    assert callable(invoke)
```
'''
    security_scan_service = _BlockingSecurityScanService()
    service = DeploymentPipelineService(
        orchestrator=_FakeOrchestrator(test_output_text=test_output),  # type: ignore[arg-type]
        session_service=_FakeSessionService(),  # type: ignore[arg-type]
        event_bus=WorkflowEventBus(),
        access_policy_service=_access_policy_service(),
        mission_identity_service=NullMissionIdentityService(),
        mission_agent_provisioning_service=NullMissionAgentProvisioningService(),
        backend_deployment_service=_FakeProtectedBackendDeploymentService(),  # type: ignore[arg-type]
        frontend_deployment_service=_FakeProtectedFrontendDeploymentService(),  # type: ignore[arg-type]
        test_execution_service=TestExecutionService(timeout_seconds=60),
        security_scan_service=security_scan_service,  # type: ignore[arg-type]
        build_workspace_root=tmp_path,
    )

    run = await service.start(
        session_id="session-1", requesting_user_id="user-1", workflow_run_id="run-1"
    )
    run = await service.wait_for_run(run.id)

    assert run.status == "completed"
    assert run.launch_url == run.frontend_url
    assert security_scan_service.calls == 0
    assert [step.step_id for step in run.steps][-2:] == [
        "execute-test-suite",
        "launch-mission",
    ]


class _ModelRecordingMissionAgentService(NullMissionAgentProvisioningService):
    def __init__(self) -> None:
        self.model_deployment_refs: list[str | None] = []

    async def provision(
        self,
        *,
        mission_slug: str,
        agent_names: list[str],
        architecture_document: str,
        on_agent_provisioned=None,
        model_deployment_ref: str | None = None,
    ):
        self.model_deployment_refs.append(model_deployment_ref)
        return await super().provision(
            mission_slug=mission_slug,
            agent_names=agent_names,
            architecture_document=architecture_document,
            on_agent_provisioned=on_agent_provisioned,
        )


async def test_provisioning_uses_the_landing_page_approved_model(tmp_path: Path):
    # The discovery workflow run's agent_scope_id carries "model:<ref>" when
    # the user selected a specific model on Genie's own Landing page (see
    # app.api.workflows.run_workflow) - Deploy & Launch must forward that
    # exact model into mission agent provisioning instead of silently
    # falling back to the platform default.
    provisioning_service = _ModelRecordingMissionAgentService()
    service = DeploymentPipelineService(
        orchestrator=_FakeOrchestrator(
            test_output_text=_PASSING_TEST_OUTPUT,
            agent_scope_id="model:claude-opus-4",
        ),  # type: ignore[arg-type]
        session_service=_FakeSessionService(),  # type: ignore[arg-type]
        event_bus=WorkflowEventBus(),
        access_policy_service=_access_policy_service(),
        mission_identity_service=NullMissionIdentityService(),
        mission_agent_provisioning_service=provisioning_service,
        backend_deployment_service=NullBackendDeploymentService(),
        frontend_deployment_service=NullFrontendDeploymentService(),
        test_execution_service=TestExecutionService(timeout_seconds=60),
        security_scan_service=SecurityScanService(timeout_seconds=60),
        build_workspace_root=tmp_path,
    )

    run = await service.start(
        session_id="session-1", requesting_user_id="user-1", workflow_run_id="run-1"
    )
    run = await service.wait_for_run(run.id)

    assert run.status == "completed"
    assert provisioning_service.model_deployment_refs == ["claude-opus-4"]


async def test_provisioning_falls_back_to_default_model_when_no_scope_was_selected(
    tmp_path: Path,
):
    provisioning_service = _ModelRecordingMissionAgentService()
    service = DeploymentPipelineService(
        orchestrator=_FakeOrchestrator(test_output_text=_PASSING_TEST_OUTPUT),  # type: ignore[arg-type]
        session_service=_FakeSessionService(),  # type: ignore[arg-type]
        event_bus=WorkflowEventBus(),
        access_policy_service=_access_policy_service(),
        mission_identity_service=NullMissionIdentityService(),
        mission_agent_provisioning_service=provisioning_service,
        backend_deployment_service=NullBackendDeploymentService(),
        frontend_deployment_service=NullFrontendDeploymentService(),
        test_execution_service=TestExecutionService(timeout_seconds=60),
        security_scan_service=SecurityScanService(timeout_seconds=60),
        build_workspace_root=tmp_path,
    )

    run = await service.start(
        session_id="session-1", requesting_user_id="user-1", workflow_run_id="run-1"
    )
    run = await service.wait_for_run(run.id)

    assert run.status == "completed"
    assert provisioning_service.model_deployment_refs == [None]


class _RecordingMissionIdentityService(NullMissionIdentityService):
    def __init__(self, delete_events: list[str]) -> None:
        self.delete_events = delete_events

    async def delete(
        self,
        *,
        identity_name: str,
        principal_id: str,
        resource_group_name: str | None = None,
        role_assignment_ids: list[str] | None = None,
    ) -> None:
        del resource_group_name, role_assignment_ids
        self.delete_events.append(f"identity:{identity_name}:{principal_id}")


class _RecordingMissionAgentService(NullMissionAgentProvisioningService):
    def __init__(self, delete_events: list[str]) -> None:
        self.delete_events = delete_events

    async def delete(self, *, foundry_agent_names: list[str]) -> None:
        self.delete_events.append(f"agents:{len(foundry_agent_names)}")


async def test_abandon_deletes_complete_prototype_boundary_in_dependency_order(
    tmp_path: Path,
):
    authenticated_test_output = '''
```python
# REQ-001
import os

def test_req_001_uses_live_prototype():
    assert os.environ["MISSION_BACKEND_URL"].startswith("https://")
```
'''
    delete_events: list[str] = []
    service = DeploymentPipelineService(
        orchestrator=_FakeOrchestrator(test_output_text=authenticated_test_output),  # type: ignore[arg-type]
        session_service=_FakeSessionService(),  # type: ignore[arg-type]
        event_bus=WorkflowEventBus(),
        access_policy_service=_access_policy_service(),
        mission_identity_service=_RecordingMissionIdentityService(delete_events),
        mission_agent_provisioning_service=_RecordingMissionAgentService(delete_events),
        backend_deployment_service=_FakeProtectedBackendDeploymentService(delete_events),  # type: ignore[arg-type]
        frontend_deployment_service=_FakeProtectedFrontendDeploymentService(delete_events),  # type: ignore[arg-type]
        test_execution_service=TestExecutionService(timeout_seconds=60),
        security_scan_service=SecurityScanService(timeout_seconds=60),
        build_workspace_root=tmp_path,
    )
    run = await service.start(
        session_id="session-1", requesting_user_id="user-1", workflow_run_id="run-1"
    )
    run = await service.wait_for_run(run.id)
    mission_slug = f"acme-mission-{run.id[:8]}"

    await service.abandon(pipeline_run_id=run.id, mission_title="Acme Mission")

    assert delete_events == [
        f"backend:{mission_slug}",
        f"frontend:{mission_slug}",
        "agents:2",
        (
            f"identity:local-genie-mission-{mission_slug[:16]}:"
            f"local-principal-{mission_slug}"
        ),
    ]
    assert service.get_run(run.id) is None
    assert not (tmp_path / run.id).exists()


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


async def test_pipeline_blocks_passing_tests_that_omit_an_approved_requirement(
    tmp_path: Path,
) -> None:
    service = _build_service(
        requirements_output="[REQ-001] Search documents.\n[REQ-002] Export results.",
        test_output_text="""
```python
# covers REQ-001
def test_search_documents():
    assert 1 + 1 == 2
```
""",
        tmp_path=tmp_path,
    )

    run = await service.start(
        session_id="session-1",
        requesting_user_id="user-1",
        workflow_run_id="run-1",
    )
    run = await service.wait_for_run(run.id)

    assert run.status == "failed", [
        (step.step_id, step.status, step.error) for step in run.steps
    ]
    failed_step = next(step for step in run.steps if step.step_id == "generate-test-suite")
    assert failed_step.status == "failed"
    assert failed_step.error is not None
    assert "REQ-002" in failed_step.error


async def test_pipeline_launches_at_ninety_percent_and_preserves_requirement_gaps(
    tmp_path: Path,
) -> None:
    requirements = "\n".join(
        f"[REQ-{number:03d}] Requirement {number}." for number in range(1, 11)
    )
    covered_suite = "\n".join(
        "```python\n"
        f"# REQ-{number:03d}\n"
        f"def test_req_{number:03d}():\n"
        "    assert True\n"
        "```"
        for number in range(1, 10)
    )
    orchestrator = _RepairingFakeOrchestrator(
        test_outputs=[covered_suite],
        requirements_output=requirements,
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
        fidelity_max_repair_attempts=1,
        fidelity_min_coverage_percent=90,
    )

    run = await service.start(
        session_id="session-1", requesting_user_id="user-1", workflow_run_id="run-1"
    )
    run = await service.wait_for_run(run.id)

    assert run.status == "completed", [
        (step.step_id, step.status, step.error) for step in run.steps
    ]
    assert run.launch_url is not None
    assert run.fidelity_report is not None
    assert run.fidelity_report.status == "passed"
    assert run.fidelity_report.coverage_percent == 90
    assert run.fidelity_report.pass_percent == 100
    assert run.fidelity_report.gaps == ["REQ-010: no executable acceptance test"]
    assert len(orchestrator.execute_agent_calls) == 1


def test_generated_frontend_runs_pinned_impeccable_detector_before_build() -> None:
    from app.deploy_launch.pipeline_service import _FRONTEND_PACKAGE_JSON

    assert '"impeccable": "3.6.0"' in _FRONTEND_PACKAGE_JSON
    assert '"vite": "6.4.3"' in _FRONTEND_PACKAGE_JSON
    assert '"design:check": "impeccable detect MissionApp.tsx src/"' in _FRONTEND_PACKAGE_JSON
    assert '"build": "npm run design:check && vite build"' in _FRONTEND_PACKAGE_JSON


def test_generated_mission_shell_uses_one_visual_system_without_nested_cards() -> None:
    from app.deploy_launch.pipeline_service import _FRONTEND_MAIN_TSX, _FRONTEND_STYLES_CSS

    assert '<section className="genie-input-surface genie-fade-in">' in _FRONTEND_MAIN_TSX
    assert '<section className="genie-card genie-fade-in">' not in _FRONTEND_MAIN_TSX
    assert "--genie-surface: #ffffff;" in _FRONTEND_STYLES_CSS
    assert "--genie-text: #172033;" in _FRONTEND_STYLES_CSS
    assert "--genie-accent: #185abd;" in _FRONTEND_STYLES_CSS
    assert "color-scheme: light;" in _FRONTEND_STYLES_CSS
    assert ".genie-input-surface {" in _FRONTEND_STYLES_CSS
    assert "background-color: var(--genie-surface);" in _FRONTEND_STYLES_CSS
    assert "box-shadow: var(--genie-shadow);" in _FRONTEND_STYLES_CSS
    assert ".genie-input-surface form > section {" in _FRONTEND_STYLES_CSS
    assert "background: transparent !important;" in _FRONTEND_STYLES_CSS
    assert ".genie-input-surface fieldset label {" in _FRONTEND_STYLES_CSS
    assert '.genie-input-surface [role="alert"] {' in _FRONTEND_STYLES_CSS
    assert "background-color: var(--genie-danger-soft);" in _FRONTEND_STYLES_CSS
    assert '.genie-input-surface :where(input, textarea, select) {' in _FRONTEND_STYLES_CSS
    assert "grid-template-columns: minmax(0, 1fr);" in _FRONTEND_STYLES_CSS
    assert "overflow-wrap: anywhere;" in _FRONTEND_STYLES_CSS
    assert _FRONTEND_STYLES_CSS.index("@media (max-width: 640px)") > (
        _FRONTEND_STYLES_CSS.index(".genie-pipeline {")
    )


async def test_pipeline_accumulates_test_coverage_across_repair_retries(
    tmp_path: Path,
) -> None:
    """Each coverage-repair retry only asks for the still-missing IDs; their
    modules must be ACCUMULATED alongside earlier completions, never
    replace them - otherwise large requirement sets can never converge
    (a live 39-requirement mission stayed 26 IDs short even after every
    repair attempt, because each retry discarded already-covered tests)."""
    requirements = "[REQ-001] Process every document.\n[REQ-002] Export a report."
    first_suite = """
```python
# REQ-001
def test_req_001_processes_every_document():
    assert 1 == 1
```
"""
    second_suite = """
```python
# REQ-002
def test_req_002_exports_a_report():
    assert 1 == 1
```
"""
    orchestrator = _RepairingFakeOrchestrator(
        test_outputs=[first_suite, second_suite],
        requirements_output=requirements,
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
        fidelity_max_repair_attempts=3,
    )

    run = await service.start(
        session_id="session-1", requesting_user_id="user-1", workflow_run_id="run-1"
    )
    run = await service.wait_for_run(run.id)

    assert run.status == "completed", [
        (step.step_id, step.status, step.error) for step in run.steps
    ]
    assert run.fidelity_report is not None
    assert run.fidelity_report.coverage_percent == 100



async def test_pipeline_automatically_repairs_redeploys_and_retests_before_launch(
    tmp_path: Path,
) -> None:
    requirements = "[REQ-001] Process every document."
    failing_suite = """
```python
# REQ-001
def test_req_001_processes_every_document():
    assert 1 == 2
```
"""
    passing_suite = """
```python
# REQ-001
def test_req_001_processes_every_document():
    assert 1 == 1
```
"""
    orchestrator = _RepairingFakeOrchestrator(
        test_outputs=[failing_suite, passing_suite],
        requirements_output=requirements,
    )
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
    shared_memory = _FakeSharedMemory({})
    orchestrator.memory_service = SimpleNamespace(shared=shared_memory)
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
        fidelity_max_repair_attempts=3,
    )

    run = await service.start(
        session_id="session-1",
        requesting_user_id="user-1",
        workflow_run_id="run-1",
    )
    run = await service.wait_for_run(run.id)

    assert run.status == "completed"
    assert run.launch_url is not None
    assert run.fidelity_report is not None
    assert run.fidelity_report.status == "passed"
    assert run.fidelity_report.coverage_percent == 100
    assert run.fidelity_report.pass_percent == 100
    assert run.fidelity_report.repair_attempts == 1
    assert len(orchestrator.resume_calls) == 1
    assert [write["key"] for write in shared_memory.writes] == [
        "analyze-requirements",
        "design-architecture",
    ]
    assert all(write["approval_status"] == "approved" for write in shared_memory.writes)
    repair_input = orchestrator.resume_calls[0]["build-solution"]
    assert repair_input.variables["previous_build_output"] == ""
    assert "1 failed" in repair_input.variables["user_message"]


async def test_pipeline_repairs_invalid_generated_ui_before_provisioning(
    tmp_path: Path,
) -> None:
    orchestrator = _BuildValidationRepairingFakeOrchestrator()
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
        fidelity_max_repair_attempts=3,
    )

    run = await service.start(
        session_id="session-1",
        requesting_user_id="user-1",
        workflow_run_id="run-1",
    )
    run = await service.wait_for_run(run.id)

    assert run.status == "completed"
    assert len(orchestrator.resume_calls) == 1
    repair_input = orchestrator.resume_calls[0]["build-solution"]
    assert "end-user-controlled filename" in repair_input.variables["user_message"]
    assert run.steps[1].status == "completed"


async def test_pipeline_exposes_validation_evidence_after_build_repair_is_exhausted(
    tmp_path: Path,
) -> None:
    orchestrator = _RepairingFakeOrchestrator(
        test_outputs=[_PASSING_TEST_OUTPUT],
        requirements_output=_REQUIREMENTS_OUTPUT,
    )
    invalid_build = _BUILD_OUTPUT.replace(
        "export function MissionApp() {",
        'export function MissionApp() {\n    const invalid = file.name !== "fixed.json";',
    )
    orchestrator._run.step_results[-1] = _completed_step(
        "build-solution", "genie-orchestrator", invalid_build
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
        fidelity_max_repair_attempts=1,
    )

    run = await service.start(
        session_id="session-1", requesting_user_id="user-1", workflow_run_id="run-1"
    )
    run = await service.wait_for_run(run.id)

    assert run.status == "failed"
    failed_step = next(step for step in run.steps if step.step_id == "provision-foundry-agents")
    assert failed_step.status == "failed"
    assert failed_step.error is not None
    assert "after 1 automatic repair attempt(s)" in failed_step.error
    assert "end-user-controlled filename" in failed_step.error


async def test_pipeline_fails_closed_after_fidelity_repair_budget_is_exhausted(
    tmp_path: Path,
) -> None:
    requirements = "[REQ-001] Process every document."
    failing_suite = """
```python
# REQ-001
def test_req_001_processes_every_document():
    assert 1 == 2
```
"""
    orchestrator = _RepairingFakeOrchestrator(
        test_outputs=[failing_suite, failing_suite],
        requirements_output=requirements,
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
        fidelity_max_repair_attempts=1,
    )

    run = await service.start(
        session_id="session-1",
        requesting_user_id="user-1",
        workflow_run_id="run-1",
    )
    run = await service.wait_for_run(run.id)

    assert run.status == "failed"
    assert run.launch_url is None
    assert run.fidelity_report is not None
    assert run.fidelity_report.status == "failed"
    assert run.fidelity_report.repair_attempts == 1
    assert run.fidelity_report.pass_percent == 0
    assert len(orchestrator.resume_calls) == 1
    launch_step = next(step for step in run.steps if step.step_id == "launch-mission")
    assert launch_step.status == "pending"


class _FakeHttpsBackendDeploymentService:
    """Returns a real https:// backend_url so the generate-test-suite step's
    real-action check (never active for the http://localhost Null service)
    actually runs."""

    async def deploy(
        self,
        *,
        mission_slug: str,
        build_root: Path,
        mission_identity_resource_id: str | None = None,
        on_progress=None,
    ) -> BackendDeploymentResult:
        return BackendDeploymentResult(
            image_tag=f"acr/{mission_slug}:dev",
            backend_url=f"https://{mission_slug}-backend.example.com",
        )

    async def configure_gateway_frontend_origin(
        self, *, mission_slug: str, frontend_origin: str
    ) -> None:
        del mission_slug, frontend_origin


async def test_pipeline_retries_test_generation_when_it_uses_mocks_then_succeeds(
    tmp_path: Path,
) -> None:
    requirements = "[REQ-001] Process every document."
    mock_based_suite = """
```python
# REQ-001
from unittest.mock import MagicMock

def test_req_001_processes_every_document():
    client = MagicMock()
    assert client is not None
```
"""
    real_action_suite = """
```python
# REQ-001
import httpx
import os

def test_req_001_processes_every_document():
    backend_url = os.environ.get("MISSION_BACKEND_URL", "")
    invoke_url = backend_url + "/invoke"
    def invoke():
        return httpx.post(invoke_url, json={"message": "{}", "attachments": []})
    assert invoke_url.endswith("/invoke")
    assert callable(invoke)
```
"""
    orchestrator = _RepairingFakeOrchestrator(
        test_outputs=[mock_based_suite, real_action_suite],
        requirements_output=requirements,
    )
    service = DeploymentPipelineService(
        orchestrator=orchestrator,  # type: ignore[arg-type]
        session_service=_FakeSessionService(),  # type: ignore[arg-type]
        event_bus=WorkflowEventBus(),
        access_policy_service=_access_policy_service(),
        mission_identity_service=NullMissionIdentityService(),
        mission_agent_provisioning_service=NullMissionAgentProvisioningService(),
        backend_deployment_service=_FakeHttpsBackendDeploymentService(),
        frontend_deployment_service=NullFrontendDeploymentService(),
        test_execution_service=TestExecutionService(timeout_seconds=60),
        security_scan_service=SecurityScanService(timeout_seconds=60),
        build_workspace_root=tmp_path,
        fidelity_max_repair_attempts=3,
    )

    run = await service.start(
        session_id="session-1", requesting_user_id="user-1", workflow_run_id="run-1"
    )
    run = await service.wait_for_run(run.id)

    assert run.status == "completed", [
        (step.step_id, step.status, step.error) for step in run.steps
    ]
    test_generation_calls = [
        call for call in orchestrator.execute_agent_calls if call["agent_id"] == "test-generation-agent"
    ]
    assert len(test_generation_calls) == 2
    assert "mock" in test_generation_calls[1]["variables"]["user_message"].lower()


async def test_pipeline_fails_closed_when_test_generation_keeps_using_mocks(
    tmp_path: Path,
) -> None:
    requirements = "[REQ-001] Process every document."
    mock_based_suite = """
```python
# REQ-001
from unittest.mock import MagicMock

def test_req_001_processes_every_document():
    client = MagicMock()
    assert client is not None
```
"""
    orchestrator = _RepairingFakeOrchestrator(
        test_outputs=[mock_based_suite, mock_based_suite],
        requirements_output=requirements,
    )
    service = DeploymentPipelineService(
        orchestrator=orchestrator,  # type: ignore[arg-type]
        session_service=_FakeSessionService(),  # type: ignore[arg-type]
        event_bus=WorkflowEventBus(),
        access_policy_service=_access_policy_service(),
        mission_identity_service=NullMissionIdentityService(),
        mission_agent_provisioning_service=NullMissionAgentProvisioningService(),
        backend_deployment_service=_FakeHttpsBackendDeploymentService(),
        frontend_deployment_service=NullFrontendDeploymentService(),
        test_execution_service=TestExecutionService(timeout_seconds=60),
        security_scan_service=SecurityScanService(timeout_seconds=60),
        build_workspace_root=tmp_path,
        fidelity_max_repair_attempts=1,
    )

    run = await service.start(
        session_id="session-1", requesting_user_id="user-1", workflow_run_id="run-1"
    )
    run = await service.wait_for_run(run.id)

    assert run.status == "failed"
    failed_step = next(step for step in run.steps if step.step_id == "generate-test-suite")
    assert failed_step.status == "failed"
    assert failed_step.error is not None
    assert "not real-action tests" in failed_step.error
    test_generation_calls = [
        call for call in orchestrator.execute_agent_calls if call["agent_id"] == "test-generation-agent"
    ]
    assert len(test_generation_calls) == 2


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


async def test_start_reuses_an_active_run_for_the_same_workflow(tmp_path: Path):
    service = _build_service(test_output_text=_PASSING_TEST_OUTPUT, tmp_path=tmp_path)

    first = await service.start(
        session_id="session-1", requesting_user_id="user-1", workflow_run_id="run-1"
    )
    duplicate = await service.start(
        session_id="session-1", requesting_user_id="user-1", workflow_run_id="run-1"
    )

    assert duplicate.id == first.id
    assert service.list_runs_for_session("session-1") == [first]

    await service.wait_for_run(first.id)


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

    async def configure_gateway_frontend_origin(
        self, *, mission_slug: str, frontend_origin: str
    ) -> None:
        del mission_slug, frontend_origin


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


async def test_retry_with_no_matching_failed_run_restarts_from_the_first_step(tmp_path: Path):
    """Regression test: if a caller asks to resume a specific step (e.g. a
    "Retry" click) but no matching in-memory failed run exists for that
    session/workflow - most realistically because this process restarted
    and lost every in-memory run/workspace/generated-test-output - honoring
    that resume point against a brand-new pipeline_run would silently skip
    every earlier step without them ever having actually run on this new
    object (e.g. jumping straight to "execute-test-suite" with no generated
    tests, producing a false "Running 0 generated test module(s)" /
    "fidelity report unavailable" failure). It must instead fail safe and
    restart the fresh run from the very first step."""
    service = _build_service(test_output_text=_PASSING_TEST_OUTPUT, tmp_path=tmp_path)

    run = await service.start(
        session_id="session-1",
        requesting_user_id="user-1",
        workflow_run_id="run-1",
        resume_from_step="execute-test-suite",
    )
    run = await service.wait_for_run(run.id)

    assert run.status == "completed"
    assert all(step.status == "completed" for step in run.steps)
    assert run.fidelity_report is not None
    assert run.fidelity_report.status == "passed"


async def test_completed_prototype_inventory_rehydrates_after_restart(tmp_path: Path):
    repository = InMemoryDeploymentRunRepository()
    service = _build_service(
        test_output_text=_PASSING_TEST_OUTPUT,
        tmp_path=tmp_path,
        run_repository=repository,
    )
    run = await service.start(
        session_id="session-1",
        requesting_user_id="tenant-1:object-1",
        requesting_tenant_id="tenant-1",
        requesting_object_id="object-1",
        workflow_run_id="run-1",
    )
    run = await service.wait_for_run(run.id)

    restarted = _build_service(
        test_output_text=_PASSING_TEST_OUTPUT,
        tmp_path=tmp_path,
        run_repository=repository,
    )
    await restarted.initialize()

    restored = restarted.get_run(run.id)
    assert restored is not None
    assert restored.status == "completed"
    assert restored.owner_user_id == "tenant-1:object-1"
    assert restored.owner_tenant_id == "tenant-1"
    assert restored.owner_object_id == "object-1"
    assert restored.expires_at is not None


async def test_interrupted_prototype_fails_closed_when_inventory_rehydrates(tmp_path: Path):
    repository = InMemoryDeploymentRunRepository()
    now = datetime.now(UTC)
    interrupted = DeploymentPipelineRun(
        id="prototype-1",
        session_id="session-1",
        workflow_run_id="workflow-1",
        owner_user_id="tenant-1:object-1",
        status="running",
        steps=[
            DeploymentStepResult(
                step_id="deploy-backend-service",
                name="Deploy Backend Service",
                status="running",
                started_at=now,
            )
        ],
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=7),
    )
    await repository.put(interrupted)
    restarted = _build_service(
        test_output_text=_PASSING_TEST_OUTPUT,
        tmp_path=tmp_path,
        run_repository=repository,
    )

    await restarted.initialize()

    restored = restarted.get_run(interrupted.id)
    assert restored is not None
    assert restored.status == "failed"
    assert restored.steps[0].status == "failed"
    assert restored.steps[0].error == "Deployment was interrupted by a service restart."


async def test_owner_cannot_exceed_active_prototype_limit(tmp_path: Path):
    service = _build_service(
        test_output_text=_PASSING_TEST_OUTPUT,
        tmp_path=tmp_path,
        prototype_max_active_per_owner=1,
    )
    first = await service.start(
        session_id="session-1",
        requesting_user_id="tenant-1:object-1",
        workflow_run_id="run-1",
    )
    await service.wait_for_run(first.id)

    with pytest.raises(DeploymentPipelineStepFailedError, match="limit reached"):
        await service.start(
            session_id="session-2",
            requesting_user_id="tenant-1:object-1",
            workflow_run_id="run-1",
        )


async def test_cleanup_expired_deletes_terminal_prototype(tmp_path: Path):
    repository = InMemoryDeploymentRunRepository()
    service = _build_service(
        test_output_text=_PASSING_TEST_OUTPUT,
        tmp_path=tmp_path,
        run_repository=repository,
    )
    run = await service.start(
        session_id="session-1",
        requesting_user_id="tenant-1:object-1",
        workflow_run_id="run-1",
    )
    run = await service.wait_for_run(run.id)
    run.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await repository.put(run)

    deleted = await service.cleanup_expired()

    assert deleted == [run.id]
    assert service.get_run(run.id) is None
    assert await repository.list_all() == []


class _DeleteFailingBackendDeploymentService(NullBackendDeploymentService):
    async def delete(self, *, mission_slug: str, app_name: str | None = None) -> None:
        del app_name
        raise BackendDeploymentError(f"Cannot delete {mission_slug}.")


async def test_cleanup_failure_remains_in_inventory_for_retry(tmp_path: Path):
    repository = InMemoryDeploymentRunRepository()
    service = _build_service(
        test_output_text=_PASSING_TEST_OUTPUT,
        tmp_path=tmp_path,
        run_repository=repository,
        backend_deployment_service=_DeleteFailingBackendDeploymentService(),
    )
    run = await service.start(
        session_id="session-1",
        requesting_user_id="tenant-1:object-1",
        workflow_run_id="run-1",
    )
    run = await service.wait_for_run(run.id)
    run.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    deleted = await service.cleanup_expired()

    assert deleted == []
    retained = service.get_run(run.id)
    assert retained is not None
    assert retained.cleanup_status == "deletion_failed"
    assert retained.cleanup_error == f"Cannot delete {run.mission_slug}."
    assert [stored.id for stored in await repository.list_all()] == [run.id]


def test_frontend_main_tsx_renders_a_gamified_multi_input_mission_queue():
    """The deterministic mission shell must never regress back to a plain
    single-textbox/single-response "document" UI: every generated mission
    frontend needs a drag-and-drop-capable Mission Queue where each typed
    request or uploaded file becomes its own independent, auto-run item with
    its own step-by-step phase label - not one shared response blob. This
    also guards against the Python-template escape-doubling bug class (an
    undoubled ``\\n``/``\\n\\n`` here would corrupt the SSE frame-splitting
    logic silently at template-definition time).
    """
    from app.deploy_launch.pipeline_service import _FRONTEND_MAIN_TSX, _FRONTEND_STYLES_CSS

    assert "type QueueItem = {" in _FRONTEND_MAIN_TSX
    assert 'type QueueItemStatus = "queued" | "running" | "complete" | "error"' in _FRONTEND_MAIN_TSX
    assert "async function runItem(item: QueueItem)" in _FRONTEND_MAIN_TSX
    assert "body: JSON.stringify({ message: item.message, attachments: item.attachments })" in _FRONTEND_MAIN_TSX
    assert "function addMessageToQueue()" in _FRONTEND_MAIN_TSX
    assert "async function addFilesToQueue(files: FileList | File[])" in _FRONTEND_MAIN_TSX
    assert "onDrop={handleDrop}" in _FRONTEND_MAIN_TSX
    assert 'frames = buffer.split("\\n\\n")' in _FRONTEND_MAIN_TSX
    assert "Waiting in queue" in _FRONTEND_MAIN_TSX
    assert "Contacting mission backend" in _FRONTEND_MAIN_TSX
    assert "Agents collaborating" in _FRONTEND_MAIN_TSX

    assert ".genie-dropzone {" in _FRONTEND_STYLES_CSS
    assert ".genie-dropzone-active {" in _FRONTEND_STYLES_CSS
    assert ".genie-queue-grid {" in _FRONTEND_STYLES_CSS
    assert ".genie-queue-item {" in _FRONTEND_STYLES_CSS
    assert ".genie-hero {" in _FRONTEND_STYLES_CSS


def test_frontend_main_tsx_gives_custom_ui_an_onsubmit_contract_and_animated_pipeline():
    """Regression guard for the "prototype looks pathetic" fix: any custom,
    mission-specific component the Build Agent generates must never build its
    own duplicate live-agent/output panel - it only collects input and hands
    it off via a single ``onSubmit`` prop, and the shell's real "Agent
    Pipeline" must be an animated, explained visualization (not a static row
    of badges) so the live hand-offs actually read as alive.
    """
    from app.deploy_launch.pipeline_service import _FRONTEND_MAIN_TSX, _FRONTEND_STYLES_CSS

    # The generated component receives a typed onSubmit contract, never its
    # own /invoke/stream wiring - it hands control straight to the shell's
    # real Mission Queue.
    assert "type MissionAppProps = {" in _FRONTEND_MAIN_TSX
    assert "onSubmit: (message: string, attachments?: Attachment[]) => void;" in _FRONTEND_MAIN_TSX
    assert "type GeneratedComponent = React.ComponentType<Partial<MissionAppProps>>;" in _FRONTEND_MAIN_TSX
    assert "function submitFromCustomUI(message: string, attachments?: Attachment[])" in _FRONTEND_MAIN_TSX
    assert "<GeneratedMissionApp onSubmit={submitFromCustomUI} missionAgents={missionAgents} />" in _FRONTEND_MAIN_TSX
    assert '<h2 className="genie-zone-title">Mission Input</h2>' in _FRONTEND_MAIN_TSX

    # The Agent Pipeline is now an animated node/connector visualization with
    # an explanatory caption, not a static badge row.
    assert "genie-pipeline-caption" in _FRONTEND_MAIN_TSX
    assert 'className="genie-pipeline"' in _FRONTEND_MAIN_TSX
    assert "genie-pipeline-node genie-pipeline-node-" in _FRONTEND_MAIN_TSX
    assert "genie-pipeline-connector" in _FRONTEND_MAIN_TSX

    # The generic composer is de-emphasized into a collapsed disclosure once
    # a real custom mission-input form exists, instead of competing with it.
    assert '<details className="genie-card genie-quick-request">' in _FRONTEND_MAIN_TSX
    assert "Quick request (send a message or file directly)" in _FRONTEND_MAIN_TSX

    assert ".genie-pipeline {" in _FRONTEND_STYLES_CSS
    assert ".genie-pipeline-node-active {" in _FRONTEND_STYLES_CSS
    assert ".genie-pipeline-node-complete {" in _FRONTEND_STYLES_CSS
    assert ".genie-pipeline-connector-active {" in _FRONTEND_STYLES_CSS
    assert "@keyframes genie-pipeline-node-pulse {" in _FRONTEND_STYLES_CSS
    assert "@keyframes genie-pipeline-particle-travel {" in _FRONTEND_STYLES_CSS
    assert ".genie-quick-request-summary {" in _FRONTEND_STYLES_CSS


def test_frontend_main_tsx_confines_a_generated_ui_crash_to_an_error_boundary():
    """Regression guard: the Build Agent's generated component is untrusted
    LLM-authored code and can have real runtime bugs (e.g. a ReferenceError
    from an undeclared variable). Without an error boundary, React unmounts
    the entire Mission Control page on such a crash - this asserts the
    generated component is wrapped so only its own zone fails, leaving the
    Quick Request fallback and the rest of the shell usable."""
    from app.deploy_launch.pipeline_service import _FRONTEND_MAIN_TSX

    assert "class MissionInputBoundary extends React.Component<" in _FRONTEND_MAIN_TSX
    assert "static getDerivedStateFromError() {" in _FRONTEND_MAIN_TSX
    assert "componentDidCatch(error: unknown) {" in _FRONTEND_MAIN_TSX
    assert (
        "<MissionInputBoundary>\n"
        "                    <GeneratedMissionApp onSubmit={submitFromCustomUI} "
        "missionAgents={missionAgents} />\n"
        "                </MissionInputBoundary>"
    ) in _FRONTEND_MAIN_TSX

