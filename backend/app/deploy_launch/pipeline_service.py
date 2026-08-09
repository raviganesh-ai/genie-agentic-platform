"""Deploy & Launch pipeline orchestration.

``DeploymentPipelineService`` is the real, deterministic, code-driven glue
that executes every step in ``DEPLOYMENT_STEP_ORDER`` (see
``app.deploy_launch.models``) against real Azure SDKs - or their Null/local
equivalents selected by ``Settings.provider_mode``, mirroring
``AzureAgentGateway``/``LocalAgentGateway`` - never fabricating a step's
result. This is explicitly NOT an LLM-driven workflow step: it is invoked
only after the ``solution-discovery-workflow`` has already produced an
approved architecture (``design-architecture``), generated build
(``build-solution``), and generated test suite (``test-generation``).

Approval gating mirrors ``WorkflowRuntime._enforce_approval_gate`` exactly:
the first time ``start()`` is called for a workflow run with no existing
``final-output-approval`` request, one is auto-created (status
``pending``) and ``DeploymentApprovalPendingError`` is raised so the caller
can direct a human reviewer to decide it via the existing
``POST /sessions/{session_id}/approvals/{request_id}/decide`` endpoint;
only a request whose decision is ``approved`` allows the pipeline to run.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final
from uuid import uuid4

from app.config.settings import Settings
from app.deploy_launch.access_policy_service import AccessPolicyService
from app.deploy_launch.backend_deployment_service import (
    BackendDeploymentError,
    BackendDeploymentService,
    NullBackendDeploymentService,
)
from app.deploy_launch.code_materializer import (
    MaterializedBuild,
    MaterializedCodeError,
    generate_backend_service_scaffold,
    materialize_build,
)
from app.deploy_launch.frontend_deployment_service import (
    FrontendDeploymentError,
    FrontendDeploymentService,
    NullFrontendDeploymentService,
)
from app.deploy_launch.mission_agent_provisioning_service import (
    MissionAgentProvisioningError,
    MissionAgentProvisioningService,
    NullMissionAgentProvisioningService,
)
from app.deploy_launch.models import (
    DEPLOYMENT_STEP_NAMES,
    DEPLOYMENT_STEP_ORDER,
    DeploymentPipelineRun,
    DeploymentStepId,
    DeploymentStepResult,
)
from app.deploy_launch.security_scan_service import SecurityScanService
from app.deploy_launch.test_execution_service import TestExecutionService, extract_test_modules
from app.governance.approval_service import ApprovalService
from app.models.workflow_models import WorkflowRunResult
from app.models.workflow_stream_models import WorkflowStreamEvent, WorkflowStreamEventType
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.orchestration.workflow_event_bus import WorkflowEventBus
from app.services.session_service import SessionService
from app.services.workshop_service import UnknownWorkflowRunError

__all__ = [
    "DeploymentApprovalBlockedError",
    "DeploymentApprovalPendingError",
    "DeploymentPipelineService",
    "DeploymentPipelineStepFailedError",
    "create_deployment_pipeline_service",
]

_PIPELINE_AGENT_ID: Final = "deploy-launch-pipeline"

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


class DeploymentApprovalPendingError(RuntimeError):
    """Raised when the ``final-output-approval`` checkpoint has not yet been decided."""


class DeploymentApprovalBlockedError(RuntimeError):
    """Raised when the ``final-output-approval`` checkpoint was explicitly rejected/expired."""


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
        approval_service: ApprovalService,
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
        final_output_approval_checkpoint_id: str = "final-output-approval",
    ) -> None:
        self._orchestrator = orchestrator
        self._session_service = session_service
        self._approval_service = approval_service
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
        self._final_output_approval_checkpoint_id = final_output_approval_checkpoint_id
        self._runs: dict[str, DeploymentPipelineRun] = {}
        self._workspaces: dict[str, _RunWorkspace] = {}
        self._materialized_builds: dict[str, MaterializedBuild] = {}
        self._agent_foundry_names: dict[str, dict[str, str]] = {}

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
        """Runs every Deploy & Launch step in order, once the final-output
        approval checkpoint has been granted. Raises
        ``DeploymentApprovalPendingError``/``DeploymentApprovalBlockedError``
        (fail closed) if it has not."""

        await self._session_service.get_session(
            session_id=session_id, requesting_user_id=requesting_user_id
        )
        run = await self._get_workflow_run(workflow_run_id)
        resolved_trace_id = trace_id or str(uuid4())

        run = await self._ensure_upstream_steps_completed(
            run=run, session_id=session_id, trace_id=resolved_trace_id
        )

        await self._ensure_final_output_approval_granted(
            session_id=session_id, workflow_run_id=workflow_run_id, trace_id=resolved_trace_id
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

        mission_slug = f"mission-{pipeline_run.id[:8]}"
        backend_root = self._build_workspace_root / pipeline_run.id / "backend"
        frontend_root = self._build_workspace_root / pipeline_run.id / "frontend"
        self._workspaces[pipeline_run.id] = _RunWorkspace(
            backend_root=backend_root, frontend_root=frontend_root
        )

        try:
            await self._execute_steps(
                pipeline_run=pipeline_run,
                run=run,
                mission_slug=mission_slug,
                backend_root=backend_root,
                frontend_root=frontend_root,
            )
        except Exception:
            pipeline_run.status = "failed"
            pipeline_run.updated_at = datetime.now(UTC)
            raise

        pipeline_run.status = "completed"
        pipeline_run.updated_at = datetime.now(UTC)
        return pipeline_run

    async def _ensure_final_output_approval_granted(
        self, *, session_id: str, workflow_run_id: str, trace_id: str
    ) -> None:
        requests = await self._approval_service.list_requests_for_session(session_id)
        matching = [
            request
            for request in requests
            if request.checkpoint_id == self._final_output_approval_checkpoint_id
            and request.subject_id == workflow_run_id
        ]

        if any(request.status == "approved" for request in matching):
            return

        if any(request.status == "pending" for request in matching):
            raise DeploymentApprovalPendingError(
                f"Deploy & Launch checkpoint '{self._final_output_approval_checkpoint_id}' "
                f"is still awaiting a decision for workflow run '{workflow_run_id}'."
            )

        if matching:
            raise DeploymentApprovalBlockedError(
                f"Deploy & Launch checkpoint '{self._final_output_approval_checkpoint_id}' "
                f"was not granted for workflow run '{workflow_run_id}'."
            )

        await self._approval_service.request_approval(
            checkpoint_id=self._final_output_approval_checkpoint_id,
            session_id=session_id,
            trace_id=trace_id,
            requested_by_agent_id=_PIPELINE_AGENT_ID,
            subject_type="workflow_run",
            subject_id=workflow_run_id,
        )
        raise DeploymentApprovalPendingError(
            f"Deploy & Launch checkpoint '{self._final_output_approval_checkpoint_id}' "
            f"was just requested for workflow run '{workflow_run_id}' and is awaiting a decision."
        )

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
        """
        required_step_ids = (self._build_step_id, self._test_generation_step_id)
        if all(self._step_completed(run, step_id) for step_id in required_step_ids):
            return run

        return await self._orchestrator.resume_workflow(
            workflow_run_id=run.workflow_run_id,
            session_id=session_id,
            trace_id=trace_id,
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
                    provisioned = await self._mission_agent_provisioning_service.provision(
                        mission_slug=mission_slug,
                        agent_names=agent_names,
                        architecture_document=architecture_document,
                    )
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
                    detail = f"Backend deployed at {backend_result.backend_url}."

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
                    if test_result.ran and test_result.failed == 0 and test_result.errors == 0:
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
            except (
                MaterializedCodeError,
                MissionAgentProvisioningError,
                BackendDeploymentError,
                FrontendDeploymentError,
                UnknownWorkflowRunError,
            ) as exc:
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
    approval_service: ApprovalService,
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
        approval_service=approval_service,
        event_bus=event_bus,
        access_policy_service=access_policy_service,
        mission_agent_provisioning_service=mission_agent_provisioning_service,
        backend_deployment_service=backend_deployment_service,
        frontend_deployment_service=frontend_deployment_service,
        test_execution_service=TestExecutionService(),
        security_scan_service=SecurityScanService(),
        build_workspace_root=settings.deployment_build_workspace_root,
    )
