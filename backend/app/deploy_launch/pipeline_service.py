"""Deploy & Launch pipeline orchestration.

``DeploymentPipelineService`` is the real, deterministic, code-driven glue
that executes every step in ``DEPLOYMENT_STEP_ORDER`` (see
``app.deploy_launch.models``) against real Azure SDKs - or their Null/local
equivalents when the required settings are not configured, mirroring
``AzureAgentGateway``/``LocalAgentGateway`` - never fabricating a step's
result. This is explicitly NOT an LLM-driven workflow step: it is invoked
only after the ``solution-discovery-workflow`` has already produced an
approved architecture (``design-architecture``), generated build
(``build-solution``), and generated test suite (``test-generation``).

Genie's Deploy & Launch stage has exactly one gate: the human clicking
Start. There is no separate approval-checkpoint request/decide dance -
``start()`` runs the upstream self-heal (see
``_ensure_upstream_steps_completed``) and then immediately kicks off the
pipeline.

``start()`` returns as soon as the run is created (status
``running``) - the nine steps themselves execute in a background asyncio
task, since real Azure agent/backend/frontend deployments plus a real test
run and security scan can legitimately take far longer than any single HTTP
request should block for. Callers (the API layer, the frontend) always
observe progress by polling ``get_run``/``list_runs_for_session`` (or the
live ``WorkflowEventBus`` stream) - never by relying on ``start()`` itself
to have finished the work.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final
from uuid import uuid4

from app.config.settings import Settings
from app.deploy_launch.access_policy_service import AccessPolicyService
from app.deploy_launch.backend_deployment_service import (
    BackendDeploymentService,
    NullBackendDeploymentService,
)
from app.deploy_launch.code_materializer import (
    MaterializedBuild,
    generate_backend_service_scaffold,
    materialize_build,
)
from app.deploy_launch.frontend_deployment_service import (
    FrontendDeploymentService,
    NullFrontendDeploymentService,
)
from app.deploy_launch.mission_agent_provisioning_service import (
    MissionAgentProvisioningService,
    NullMissionAgentProvisioningService,
)
from app.deploy_launch.models import (
    DEPLOYMENT_STEP_NAMES,
    DEPLOYMENT_STEP_ORDER,
    DeploymentPipelineRun,
    DeploymentStepId,
    DeploymentStepResult,
    ProvisionedAgentStatus,
)
from app.deploy_launch.security_scan_service import SecurityScanService
from app.deploy_launch.test_execution_service import TestExecutionService, extract_test_modules
from app.models.workflow_models import WorkflowRunResult, WorkflowStepInput
from app.models.workflow_stream_models import WorkflowStreamEvent, WorkflowStreamEventType
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.orchestration.workflow_event_bus import WorkflowEventBus
from app.services.session_service import SessionService
from app.services.workshop_service import UnknownWorkflowRunError

__all__ = [
    "DeploymentPipelineService",
    "DeploymentPipelineStepFailedError",
    "create_deployment_pipeline_service",
]

_PIPELINE_AGENT_ID: Final = "deploy-launch-pipeline"


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "mission"

_INDEX_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Mission</title>
  <script src="runtime-config.js"></script>
  <script crossorigin src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
  <script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
  <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
</head>
<body>
  <div id="root"></div>
  <script type="text/babel" data-type="module" src="MissionApp.tsx"></script>
</body>
</html>
"""


class DeploymentPipelineStepFailedError(RuntimeError):
    """Raised when a pipeline step's own real result (test run, security scan) fails."""


@dataclass
class _RunWorkspace:
    """Filesystem locations materialized for one pipeline run - kept only in memory."""

    backend_root: Path
    frontend_root: Path


class DeploymentPipelineService:
    """Executes the fixed, nine-step Deploy & Launch pipeline for one mission."""

    def __init__(
        self,
        *,
        orchestrator: AgentOrchestrator,
        session_service: SessionService,
        event_bus: WorkflowEventBus,
        access_policy_service: AccessPolicyService,
        mission_agent_provisioning_service: MissionAgentProvisioningService
        | NullMissionAgentProvisioningService,
        backend_deployment_service: BackendDeploymentService | NullBackendDeploymentService,
        frontend_deployment_service: FrontendDeploymentService | NullFrontendDeploymentService,
        test_execution_service: TestExecutionService,
        security_scan_service: SecurityScanService,
        build_workspace_root: Path,
        architecture_step_id: str = "design-architecture",
        build_step_id: str = "build-solution",
        test_generation_step_id: str = "test-generation",
    ) -> None:
        self._orchestrator = orchestrator
        self._session_service = session_service
        self._event_bus = event_bus
        self._access_policy_service = access_policy_service
        self._mission_agent_provisioning_service = mission_agent_provisioning_service
        self._backend_deployment_service = backend_deployment_service
        self._frontend_deployment_service = frontend_deployment_service
        self._test_execution_service = test_execution_service
        self._security_scan_service = security_scan_service
        self._build_workspace_root = build_workspace_root
        self._architecture_step_id = architecture_step_id
        self._build_step_id = build_step_id
        self._test_generation_step_id = test_generation_step_id
        self._runs: dict[str, DeploymentPipelineRun] = {}
        self._workspaces: dict[str, _RunWorkspace] = {}
        self._materialized_builds: dict[str, MaterializedBuild] = {}
        self._agent_foundry_names: dict[str, dict[str, str]] = {}
        self._background_tasks: dict[str, asyncio.Task[None]] = {}

    def get_run(self, pipeline_run_id: str) -> DeploymentPipelineRun | None:
        return self._runs.get(pipeline_run_id)

    def list_runs_for_session(self, session_id: str) -> list[DeploymentPipelineRun]:
        return [run for run in self._runs.values() if run.session_id == session_id]

    def get_build_root(self, pipeline_run_id: str) -> Path | None:
        workspace = self._workspaces.get(pipeline_run_id)
        return workspace.backend_root if workspace else None

    async def start(
        self,
        *,
        session_id: str,
        requesting_user_id: str,
        workflow_run_id: str,
        trace_id: str | None = None,
    ) -> DeploymentPipelineRun:
        """Kicks off every Deploy & Launch step in order as soon as the human
        clicks Start - there is no separate approval checkpoint to decide.

        The nine steps themselves (real Azure agent/backend/frontend
        deployments, a real test run, a real security scan) can legitimately
        take far longer than a single HTTP request/response should ever
        block for, so - exactly like Architecture Studio's build-solution
        kickoff - this returns as soon as the run is created (status
        ``running``) and executes the steps in a background task. Callers
        must poll ``get_run``/``list_runs_for_session`` (or the
        ``WorkflowEventBus``/SSE stream) for live per-step progress, never
        the return value of this call itself; a client-side network hiccup
        on this call must never be mistaken for the pipeline itself failing.
        """

        session = await self._session_service.get_session(
            session_id=session_id, requesting_user_id=requesting_user_id
        )
        run = await self._get_workflow_run(workflow_run_id)
        resolved_trace_id = trace_id or str(uuid4())

        run = await self._ensure_upstream_steps_completed(
            run=run, session_id=session_id, trace_id=resolved_trace_id
        )

        pipeline_run = DeploymentPipelineRun(
            id=str(uuid4()),
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            status="running",
            steps=[
                DeploymentStepResult(step_id=step_id, name=DEPLOYMENT_STEP_NAMES[step_id])
                for step_id in DEPLOYMENT_STEP_ORDER
            ],
        )
        self._runs[pipeline_run.id] = pipeline_run

        # A human-readable mission slug rooted in the mission's own title (set once
        # by the user on Upload/Landing and carried through Workshop/Architecture
        # Studio) - never a generic "mission-<uuid>" string - so the Foundry agents
        # this pipeline provisions are recognizable as belonging to this mission.
        # The short run-id suffix keeps names unique across repeat/retry deploys of
        # the same mission (Foundry agent names must be unique).
        mission_slug = f"{_slugify(session.title)}-{pipeline_run.id[:8]}"
        backend_root = self._build_workspace_root / pipeline_run.id / "backend"
        frontend_root = self._build_workspace_root / pipeline_run.id / "frontend"
        self._workspaces[pipeline_run.id] = _RunWorkspace(
            backend_root=backend_root, frontend_root=frontend_root
        )

        task = asyncio.create_task(
            self._run_and_finalize(
                pipeline_run=pipeline_run,
                run=run,
                mission_slug=mission_slug,
                backend_root=backend_root,
                frontend_root=frontend_root,
            )
        )
        self._background_tasks[pipeline_run.id] = task
        task.add_done_callback(lambda _task, _id=pipeline_run.id: self._background_tasks.pop(_id, None))

        return pipeline_run

    async def _run_and_finalize(
        self,
        *,
        pipeline_run: DeploymentPipelineRun,
        run: WorkflowRunResult,
        mission_slug: str,
        backend_root: Path,
        frontend_root: Path,
    ) -> None:
        """The background task body ``start()`` schedules: runs every step,
        then always resolves the run to a terminal status (``completed`` or
        ``failed``) - this task's own exception is never re-raised anywhere
        (there is no caller left to catch it), so every failure must already
        have been recorded on the run/step themselves before this returns."""

        try:
            await self._execute_steps(
                pipeline_run=pipeline_run,
                run=run,
                mission_slug=mission_slug,
                backend_root=backend_root,
                frontend_root=frontend_root,
            )
        except Exception:  # noqa: BLE001 - top-level background-task boundary; every
            # failure must resolve the run's status here since there is no
            # synchronous caller left to catch/report it (see the docstring above).
            pipeline_run.status = "failed"
            pipeline_run.updated_at = datetime.now(UTC)
            return

        pipeline_run.status = "completed"
        pipeline_run.updated_at = datetime.now(UTC)

    async def wait_for_run(self, pipeline_run_id: str) -> DeploymentPipelineRun:
        """Awaits a still-in-flight run's background execution to finish and
        returns its final state. Normal callers (the API/frontend) always
        poll instead; this exists for callers that genuinely need the
        finished result in-process (e.g. tests)."""

        task = self._background_tasks.get(pipeline_run_id)
        if task is not None:
            await task
        return self._runs[pipeline_run_id]

    async def _get_workflow_run(self, workflow_run_id: str) -> WorkflowRunResult:
        run = await self._orchestrator.get_workflow_run(workflow_run_id)
        if run is None:
            raise UnknownWorkflowRunError(f"No workflow run '{workflow_run_id}' found.")
        return run

    def _step_completed(self, run: WorkflowRunResult, step_id: str) -> bool:
        step = next((r for r in run.step_results if r.step_id == step_id), None)
        return step is not None and step.status == "completed"

    async def _ensure_upstream_steps_completed(
        self, *, run: WorkflowRunResult, session_id: str, trace_id: str
    ) -> WorkflowRunResult:
        """Self-heals a workflow run that has not yet finished every step
        Deploy & Launch reads from (``build-solution``/``test-generation``)
        before asking the user to click Start - e.g. an earlier page's
        fire-and-forget kickoff silently never reached the server, or the
        run is merely paused on the ``build-review-approval`` checkpoint
        that Workshop's "Proceed to Deploy & Launch" action has already
        decided by the time this runs - by resuming the SAME run here
        rather than forcing the user to notice a stuck step and manually
        return to Workshop. Only a genuinely unrecoverable state (an
        undecided/rejected approval checkpoint, or a real step failure)
        still surfaces as an error, via ``_get_step_output`` once
        ``_execute_steps`` actually reads that step's output below.

        ``build-solution``'s ``policies``/``excluded_agents`` variables are
        deliberately NOT auto-derived by ``variable_sources`` in
        config/workflows/registry.yaml (see that file's comment) - every
        OTHER caller that can (re)start this step supplies them explicitly
        as a step_input override (Architecture Studio's approval handler,
        Workshop's "Re-run UI & Agent Design"). This self-heal path is
        backend-only and has no access to whatever governance-policy text
        or excluded-agent list the user typed into those frontend pages
        (never persisted server-side - see SessionContext's
        ``governancePolicies``), so it cannot reproduce the user's real
        choices. Omitting the override entirely used to hard-fail with
        ``PromptResolutionError: Prompt 'orchestrator-build-phase-v1' is
        missing required variable(s): ['excluded_agents', 'policies']`` -
        every "Retry Deploy & Launch" click hit the identical failure
        forever, since nothing about the run's state changes between
        retries. Supplying safe empty-string defaults here instead lets a
        stuck build-solution genuinely (re)run - matching the prompt/tool's
        own contract that an empty policies/excluded_agents string simply
        means "no extra policy text" / "no excluded agents" (see
        ``_parse_excluded_agent_names`` in orchestration_tools.py). Only
        ever attached when build-solution itself has NOT already
        completed - passing ANY step_input for a step re-executes it even
        if already completed (see workflow_runtime.resume_workflow), so an
        already-finished build must never be re-triggered here with blank
        overrides that could silently discard the user's real governance
        policies.
        """
        required_step_ids = (self._build_step_id, self._test_generation_step_id)
        if all(self._step_completed(run, step_id) for step_id in required_step_ids):
            return run

        step_inputs: dict[str, WorkflowStepInput] = {}
        if not self._step_completed(run, self._build_step_id):
            step_inputs[self._build_step_id] = WorkflowStepInput(
                step_id=self._build_step_id,
                variables={"policies": "", "excluded_agents": ""},
            )

        return await self._orchestrator.resume_workflow(
            workflow_run_id=run.workflow_run_id,
            session_id=session_id,
            trace_id=trace_id,
            step_inputs=step_inputs or None,
        )

    def _get_step_output(self, run: WorkflowRunResult, step_id: str) -> str:
        step = next((r for r in run.step_results if r.step_id == step_id), None)
        if step is None or step.status != "completed":
            raise UnknownWorkflowRunError(
                f"Workflow step '{step_id}' has not completed for run '{run.workflow_run_id}'."
            )
        return step.output_text or ""

    async def _execute_steps(
        self,
        *,
        pipeline_run: DeploymentPipelineRun,
        run: WorkflowRunResult,
        mission_slug: str,
        backend_root: Path,
        frontend_root: Path,
    ) -> None:
        orchestrator_foundry_name = "orchestrator"
        test_output_text = ""

        for step_id in DEPLOYMENT_STEP_ORDER:
            await self._publish(pipeline_run, step_id=step_id, event_type="step_started")
            step_result = self._step_result(pipeline_run, step_id)
            step_result.status = "running"
            step_result.started_at = datetime.now(UTC)

            try:
                if step_id == "generate-access-policy":
                    document = self._access_policy_service.generate()
                    pipeline_run.access_policy = document
                    detail = f"Generated least-access policy for {len(document.agents)} agent(s)."

                elif step_id == "provision-foundry-agents":
                    architecture_document = self._get_step_output(run, self._architecture_step_id)
                    build_output_text = self._get_step_output(run, self._build_step_id)
                    materialized = materialize_build(build_output_text)
                    agent_names = list(materialized.agent_modules.keys())
                    if materialized.orchestrator_module is not None:
                        agent_names.append("orchestrator")

                    # Mark every agent "running" before the (single, atomic)
                    # provisioning call so the UI can show a real per-agent
                    # in-progress list while it is in flight, not just the
                    # step's own aggregate status.
                    pipeline_run.provisioned_agents = [
                        ProvisionedAgentStatus(agent_name=name, status="running")
                        for name in agent_names
                    ]

                    provisioned = await self._mission_agent_provisioning_service.provision(
                        mission_slug=mission_slug,
                        agent_names=agent_names,
                        architecture_document=architecture_document,
                    )
                    provisioned_by_name = {record.agent_name: record for record in provisioned}
                    pipeline_run.provisioned_agents = [
                        ProvisionedAgentStatus(
                            agent_name=name,
                            status="completed" if name in provisioned_by_name else "failed",
                            foundry_agent_name=(
                                provisioned_by_name[name].foundry_agent_name
                                if name in provisioned_by_name
                                else None
                            ),
                        )
                        for name in agent_names
                    ]
                    orchestrator_record = next(
                        (r for r in provisioned if r.agent_name == "orchestrator"), None
                    )
                    if orchestrator_record is not None:
                        orchestrator_foundry_name = orchestrator_record.foundry_agent_name
                    self._materialized_builds[pipeline_run.id] = materialized
                    self._agent_foundry_names[pipeline_run.id] = {
                        record.agent_name: record.foundry_agent_name for record in provisioned
                    }
                    detail = f"Provisioned {len(provisioned)} Foundry agent(s) for this mission."

                elif step_id == "deploy-backend-service":
                    materialized = self._materialized_builds[pipeline_run.id]
                    scaffold = generate_backend_service_scaffold(
                        mission_title=mission_slug,
                        orchestrator_agent_name=orchestrator_foundry_name,
                        agent_foundry_names=self._agent_foundry_names[pipeline_run.id],
                    )
                    materialized.write_to_directory(backend_root, backend_service_scaffold=scaffold)
                    backend_result = await self._backend_deployment_service.deploy(
                        mission_slug=mission_slug, build_root=backend_root
                    )
                    pipeline_run.backend_url = backend_result.backend_url
                    detail = (
                        f"Backend deployed at {backend_result.backend_url}, integrated with "
                        f"orchestrator agent '{orchestrator_foundry_name}'."
                    )

                elif step_id == "sync-frontend-integration":
                    materialized = self._materialized_builds[pipeline_run.id]
                    frontend_root.mkdir(parents=True, exist_ok=True)
                    (frontend_root / "MissionApp.tsx").write_text(
                        materialized.ui_component or "", encoding="utf-8"
                    )
                    (frontend_root / "runtime-config.js").write_text(
                        f'window.__MISSION_BACKEND_URL__ = "{pipeline_run.backend_url}";\n',
                        encoding="utf-8",
                    )
                    (frontend_root / "index.html").write_text(_INDEX_HTML_TEMPLATE, encoding="utf-8")
                    detail = f"Frontend wired to real backend URL {pipeline_run.backend_url}."

                elif step_id == "deploy-frontend-app":
                    frontend_result = await self._frontend_deployment_service.deploy(
                        ui_root=frontend_root
                    )
                    pipeline_run.frontend_url = frontend_result.frontend_url
                    detail = f"Frontend deployed at {frontend_result.frontend_url}."

                elif step_id == "generate-test-suite":
                    test_output_text = self._get_step_output(run, self._test_generation_step_id)
                    modules = extract_test_modules(test_output_text)
                    detail = f"Using {len(modules)} generated test module(s) from the test-generation step."

                elif step_id == "execute-test-suite":
                    test_result = await self._test_execution_service.run_tests(
                        build_root=backend_root, test_output_text=test_output_text
                    )
                    pipeline_run.test_summary = test_result.summary
                    # ``success`` fails closed even when pytest itself exits 0
                    # (e.g. zero test functions were actually collected) - see
                    # TestExecutionResult.success's docstring.
                    if test_result.success:
                        detail = test_result.summary
                    else:
                        step_result.status = "failed"
                        step_result.error = test_result.summary
                        step_result.completed_at = datetime.now(UTC)
                        await self._publish(
                            pipeline_run, step_id=step_id, event_type="step_failed", error=test_result.summary
                        )
                        raise DeploymentPipelineStepFailedError(
                            f"Generated test suite did not pass: {test_result.summary}"
                        )

                elif step_id == "run-security-scan":
                    scan_result = await self._security_scan_service.scan(build_root=backend_root)
                    pipeline_run.security_findings_count = len(scan_result.findings)
                    if scan_result.blocking:
                        step_result.status = "failed"
                        step_result.error = scan_result.summary
                        step_result.completed_at = datetime.now(UTC)
                        await self._publish(
                            pipeline_run, step_id=step_id, event_type="step_failed", error=scan_result.summary
                        )
                        raise DeploymentPipelineStepFailedError(
                            f"Security scan found blocking findings: {scan_result.summary}"
                        )
                    detail = scan_result.summary

                elif step_id == "launch-mission":
                    pipeline_run.launch_url = pipeline_run.frontend_url
                    detail = f"Mission launched at {pipeline_run.launch_url}."

                else:  # pragma: no cover - DEPLOYMENT_STEP_ORDER is exhaustive
                    detail = ""

            except DeploymentPipelineStepFailedError:
                raise
            except Exception as exc:
                # Catch every failure here (not just the specific, expected
                # error types) - a real Azure SDK network/timeout error would
                # otherwise skip this step's own status update entirely,
                # leaving it stuck showing "Running..." forever even though
                # the pipeline as a whole has already been marked "failed"
                # (see the `except Exception` in `start()` below) - a
                # confusing, inconsistent UI. Every step must always resolve
                # to a terminal, detailed status (completed or failed).
                if step_id == "provision-foundry-agents":
                    # Provisioning is atomic (all-or-nothing, see
                    # MissionAgentProvisioningService.provision) - any agent
                    # still "running" here never actually finished.
                    pipeline_run.provisioned_agents = [
                        agent.model_copy(update={"status": "failed"})
                        if agent.status == "running"
                        else agent
                        for agent in pipeline_run.provisioned_agents
                    ]
                step_result.status = "failed"
                step_result.error = str(exc)
                step_result.completed_at = datetime.now(UTC)
                await self._publish(pipeline_run, step_id=step_id, event_type="step_failed", error=str(exc))
                raise

            step_result.status = "completed"
            step_result.detail = detail
            step_result.completed_at = datetime.now(UTC)
            await self._publish(
                pipeline_run, step_id=step_id, event_type="step_completed", output_preview=detail
            )

    def _step_result(self, pipeline_run: DeploymentPipelineRun, step_id: DeploymentStepId) -> DeploymentStepResult:
        return next(step for step in pipeline_run.steps if step.step_id == step_id)

    async def _publish(
        self,
        pipeline_run: DeploymentPipelineRun,
        *,
        step_id: DeploymentStepId,
        event_type: WorkflowStreamEventType,
        output_preview: str | None = None,
        error: str | None = None,
    ) -> None:
        await self._event_bus.publish(
            WorkflowStreamEvent(
                event_type=event_type,
                session_id=pipeline_run.session_id,
                workflow_run_id=pipeline_run.id,
                step_id=step_id,
                agent_id=_PIPELINE_AGENT_ID,
                output_preview=output_preview,
                error=error,
            )
        )


def create_deployment_pipeline_service(
    *,
    settings: Settings,
    orchestrator: AgentOrchestrator,
    session_service: SessionService,
    event_bus: WorkflowEventBus,
    access_policy_service: AccessPolicyService,
    mission_agent_provisioning_service: MissionAgentProvisioningService
    | NullMissionAgentProvisioningService,
    backend_deployment_service: BackendDeploymentService | NullBackendDeploymentService,
    frontend_deployment_service: FrontendDeploymentService | NullFrontendDeploymentService,
) -> DeploymentPipelineService:
    """Wires a ``DeploymentPipelineService`` from already-constructed collaborators.

    Every Azure-calling collaborator is constructed by its own factory
    (``create_backend_deployment_service`` etc.) before being passed in here
    - this factory only assembles the pipeline glue, it never chooses
    real-vs-Null itself.
    """

    return DeploymentPipelineService(
        orchestrator=orchestrator,
        session_service=session_service,
        event_bus=event_bus,
        access_policy_service=access_policy_service,
        mission_agent_provisioning_service=mission_agent_provisioning_service,
        backend_deployment_service=backend_deployment_service,
        frontend_deployment_service=frontend_deployment_service,
        test_execution_service=TestExecutionService(),
        security_scan_service=SecurityScanService(),
        build_workspace_root=settings.deployment_build_workspace_root,
    )
