"""Deploy & Launch pipeline orchestration.

``DeploymentPipelineService`` is the real, deterministic, code-driven glue
that executes every step in ``DEPLOYMENT_STEP_ORDER`` (see
``app.deploy_launch.models``) against real Azure SDKs - or their Null/local
equivalents when the required settings are not configured, mirroring
``AzureAgentGateway``/``LocalAgentGateway`` - never fabricating a step's
result. This is explicitly NOT an LLM-driven workflow step: it is invoked
only after the ``solution-discovery-workflow`` has already produced an
approved architecture (``design-architecture``) and generated build
(``build-solution``). Test generation happens here too, as this
pipeline's own ``generate-test-suite`` step: it calls the Test Generation
Agent directly (``AgentOrchestrator.execute_agent`` - the same
outside-any-workflow-step execution path Workshop's per-component
"Regenerate" action already uses) against the real, already-deployed
build, so the generated tests cover what actually got deployed rather than
a pre-deploy narrative pass. ``execute-test-suite`` then really runs those
tests and fails the pipeline closed if they don't pass.

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

from app.agents.gateway import get_enabled_agent
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
from app.deploy_launch.container_app_frontend_deployment_service import (
    ContainerAppFrontendDeploymentService,
    NullContainerAppFrontendDeploymentService,
)
from app.deploy_launch.mission_agent_provisioning_service import (
    MissionAgentProvisioningService,
    NullMissionAgentProvisioningService,
    ProvisionedMissionAgent,
)
from app.deploy_launch.mission_identity_service import (
    MissionIdentityService,
    NullMissionIdentityService,
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
from app.deploy_launch.test_execution_service import (
    TestExecutionService,
    extract_test_modules,
    has_pytest_discoverable_tests,
)
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

_FRONTEND_INDEX_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Mission Prototype</title>
  <script src="runtime-config.js"></script>
</head>
<body>
  <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
</body>
</html>
"""

_FRONTEND_PACKAGE_JSON = """{
    "private": true,
    "type": "module",
    "scripts": {"build": "vite build"},
    "dependencies": {"react": "18.3.1", "react-dom": "18.3.1"},
    "devDependencies": {"@vitejs/plugin-react": "4.3.4", "@types/react": "18.3.18", "@types/react-dom": "18.3.5", "typescript": "5.7.2", "vite": "6.0.7"}
}
"""

_FRONTEND_TSCONFIG_JSON = """{
    "compilerOptions": {
        "target": "ES2020",
        "useDefineForClassFields": true,
        "lib": ["ES2020", "DOM", "DOM.Iterable"],
        "allowJs": false,
        "skipLibCheck": true,
        "esModuleInterop": true,
        "allowSyntheticDefaultImports": true,
        "strict": false,
        "forceConsistentCasingInFileNames": true,
        "module": "ESNext",
        "moduleResolution": "Bundler",
        "resolveJsonModule": true,
        "isolatedModules": true,
        "noEmit": true,
        "jsx": "react-jsx"
    },
    "include": ["src"]
}
"""

_FRONTEND_VITE_CONFIG = """import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({ plugins: [react()] });
"""

_FRONTEND_MAIN_TSX = """import React from "react";
import { createRoot } from "react-dom/client";
import * as GeneratedModule from "../MissionApp";

type GeneratedComponent = React.ComponentType;
const moduleValue = GeneratedModule as unknown as {
    default?: GeneratedComponent;
    App?: GeneratedComponent;
    MissionApp?: GeneratedComponent;
};
const MissionApp = moduleValue.default ?? moduleValue.App ?? moduleValue.MissionApp;

if (!MissionApp) {
    throw new Error("Generated MissionApp.tsx must export a default component, App, or MissionApp.");
}

createRoot(document.getElementById("root")!).render(<MissionApp />);
"""

_FRONTEND_ENV_D_TS = """interface Window {
    __MISSION_BACKEND_URL__?: string;
}
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
        mission_identity_service: MissionIdentityService | NullMissionIdentityService,
        mission_agent_provisioning_service: MissionAgentProvisioningService
        | NullMissionAgentProvisioningService,
        backend_deployment_service: BackendDeploymentService | NullBackendDeploymentService,
        frontend_deployment_service: (
            ContainerAppFrontendDeploymentService | NullContainerAppFrontendDeploymentService
        ),
        test_execution_service: TestExecutionService,
        security_scan_service: SecurityScanService,
        build_workspace_root: Path,
        architecture_step_id: str = "design-architecture",
        build_step_id: str = "build-solution",
        requirements_step_id: str = "analyze-requirements",
        upstream_grace_check_attempts: int = 5,
        upstream_grace_check_interval_seconds: float = 2.0,
    ) -> None:
        self._orchestrator = orchestrator
        self._session_service = session_service
        self._event_bus = event_bus
        self._access_policy_service = access_policy_service
        self._mission_identity_service = mission_identity_service
        self._mission_agent_provisioning_service = mission_agent_provisioning_service
        self._backend_deployment_service = backend_deployment_service
        self._frontend_deployment_service = frontend_deployment_service
        self._test_execution_service = test_execution_service
        self._security_scan_service = security_scan_service
        self._build_workspace_root = build_workspace_root
        self._architecture_step_id = architecture_step_id
        self._build_step_id = build_step_id
        self._requirements_step_id = requirements_step_id
        self._upstream_grace_check_attempts = upstream_grace_check_attempts
        self._upstream_grace_check_interval_seconds = upstream_grace_check_interval_seconds
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
        resume_from_step: str | None = None,
    ) -> DeploymentPipelineRun:
        """Kicks off every Deploy & Launch step in order as soon as the human
        clicks Start - there is no separate approval checkpoint to decide.

        If ``resume_from_step`` is provided, the pipeline resumes from that step
        instead of starting from the first step, allowing retry/recovery from a
        failed step without re-running prior completed steps.

        The pipeline run is created (status ``running``, every step
        ``pending``) and stored - and therefore immediately visible to
        ``get_run``/``list_runs_for_session`` pollers - before ANY
        potentially slow work happens. Resolving the session/workflow run
        and self-healing a not-yet-finished upstream step (see
        ``_ensure_upstream_steps_completed``) can legitimately take a while
        (a real upstream agent resume), so that work - like the nine
        pipeline steps themselves - runs in the background task, never as
        an invisible delay before the user sees anything. Callers must poll
        (or the ``WorkflowEventBus``/SSE stream) for live per-step
        progress, never the return value of this call itself; a
        client-side network hiccup on this call must never be mistaken for
        the pipeline itself failing.
        """

        resolved_trace_id = trace_id or str(uuid4())

        # A retry (resume_from_step set) MUST continue the SAME run - i.e. the
        # same pipeline_run.id - not mint a fresh one. Every later step reads
        # its prerequisite artifacts (materialized build, provisioned Foundry
        # agent names) out of self._materialized_builds/self._agent_foundry_names,
        # both keyed by pipeline_run.id, and the run's own already-completed
        # step results (provisioned_agents, access_policy, backend_url) live on
        # the DeploymentPipelineRun object itself. Previously this always built
        # a brand-new DeploymentPipelineRun with a fresh uuid4 id here
        # regardless of resume_from_step, which orphaned all of that prior
        # work - so "retry from failed step" always failed again (a KeyError
        # the moment execution reached any step depending on earlier output),
        # even though the UI presented it as a normal retry action.
        existing_run: DeploymentPipelineRun | None = None
        if resume_from_step:
            candidates = [
                run
                for run in self._runs.values()
                if run.session_id == session_id
                and run.workflow_run_id == workflow_run_id
                and run.status == "failed"
            ]
            if candidates:
                existing_run = max(candidates, key=lambda run: run.updated_at)

        if existing_run is not None:
            pipeline_run = existing_run
            pipeline_run.status = "running"
            pipeline_run.updated_at = datetime.now(UTC)
            workspace = self._workspaces[pipeline_run.id]
            backend_root = workspace.backend_root
            frontend_root = workspace.frontend_root
        else:
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

            backend_root = self._build_workspace_root / pipeline_run.id / "backend"
            frontend_root = self._build_workspace_root / pipeline_run.id / "frontend"
            self._workspaces[pipeline_run.id] = _RunWorkspace(
                backend_root=backend_root, frontend_root=frontend_root
            )

        task = asyncio.create_task(
            self._prepare_and_run(
                pipeline_run=pipeline_run,
                requesting_user_id=requesting_user_id,
                trace_id=resolved_trace_id,
                backend_root=backend_root,
                frontend_root=frontend_root,
                resume_from_step=resume_from_step,
            )
        )
        self._background_tasks[pipeline_run.id] = task
        task.add_done_callback(lambda _task, _id=pipeline_run.id: self._background_tasks.pop(_id, None))

        return pipeline_run

    def _fail_run(self, pipeline_run: DeploymentPipelineRun, *, error: str) -> None:
        """Resolves a run to ``failed`` for a failure that happened before any
        pipeline step began executing (session/workflow-run lookup, upstream
        self-heal) - attributed to the first step so it is still visible in
        the same per-step list the user is already watching, rather than a
        silent, unexplained stall."""
        pipeline_run.status = "failed"
        pipeline_run.updated_at = datetime.now(UTC)
        if pipeline_run.steps:
            first_step = pipeline_run.steps[0]
            first_step.status = "failed"
            first_step.error = error
            first_step.completed_at = datetime.now(UTC)

    async def _prepare_and_run(
        self,
        *,
        pipeline_run: DeploymentPipelineRun,
        requesting_user_id: str,
        trace_id: str,
        backend_root: Path,
        frontend_root: Path,
        resume_from_step: str | None = None,
    ) -> None:
        """The background task body ``start()`` schedules: resolves the
        session/workflow run, self-heals any not-yet-finished upstream step,
        then runs every pipeline step - always resolving the run to a
        terminal status (``completed`` or ``failed``). This task's own
        exception is never re-raised anywhere (there is no caller left to
        catch it), so every failure must already have been recorded on the
        run/step themselves before this returns.
        
        If ``resume_from_step`` is provided, only steps from that point onward
        are executed, allowing recovery from a failed step without re-running
        prior completed steps."""

        try:
            session = await self._session_service.get_session(
                session_id=pipeline_run.session_id, requesting_user_id=requesting_user_id
            )
            run = await self._get_workflow_run(pipeline_run.workflow_run_id)
            run = await self._ensure_upstream_steps_completed(
                run=run, session_id=pipeline_run.session_id, trace_id=trace_id
            )
        except Exception as exc:  # noqa: BLE001 - top-level background-task boundary; see docstring above.
            self._fail_run(pipeline_run, error=str(exc))
            return

        # A human-readable mission slug rooted in the mission's own title (set once
        # by the user on Upload/Landing and carried through Workshop/Architecture
        # Studio) - never a generic "mission-<uuid>" string - so the Foundry agents
        # this pipeline provisions are recognizable as belonging to this mission.
        # The short run-id suffix keeps names unique across repeat/retry deploys of
        # the same mission (Foundry agent names must be unique).
        mission_slug = f"{_slugify(session.title)}-{pipeline_run.id[:8]}"

        try:
            await self._execute_steps(
                pipeline_run=pipeline_run,
                run=run,
                mission_slug=mission_slug,
                backend_root=backend_root,
                frontend_root=frontend_root,
                trace_id=trace_id,
                resume_from_step=resume_from_step,
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

    async def _step_completed(self, run: WorkflowRunResult, step_id: str, *, trace_id: str) -> bool:
        """A step counts as done once its real output exists anywhere - the
        official ``WorkflowRunResult`` step_results entry (``status ==
        "completed"``), or - faster, and what actually matters for Deploy &
        Launch - Shared Collaboration Memory already holds that step's real
        specialist output (see ``_read_step_memory_output``). Genie's only
        real gate on Deploy & Launch is the human's own review/approval on
        Workshop (see the module docstring): once the human can see and
        approve the real generated code, this pipeline must never impose an
        additional backend-side wait of its own for the same content to be
        echoed back a second time by genie-orchestrator.
        """
        step = next((r for r in run.step_results if r.step_id == step_id), None)
        if step is not None and step.status == "completed":
            return True
        memory_output = await self._read_step_memory_output(
            session_id=run.session_id, trace_id=trace_id, step_id=step_id
        )
        return memory_output is not None

    async def _read_step_memory_output(
        self, *, session_id: str, trace_id: str, step_id: str
    ) -> str | None:
        """Reads a workflow step's real specialist output straight out of
        Shared Collaboration Memory, if the delegation tool call that
        produced it has already written it there (see
        ``app.agents.tools.orchestration_tools``'s ``_delegate``) - which
        happens the instant that specialist's own real generation finishes,
        well before genie-orchestrator's own separate, slower
        echo-completion turn resolves the workflow step's own
        ``WorkflowStepResult``. Returns ``None`` when nothing is written yet
        (or memory is unavailable), so callers fall back to the official
        step status instead.
        """
        # agent_registry/memory_service are always present on the real
        # AgentOrchestrator (see its constructor) but are accessed
        # defensively here - via getattr, never a direct attribute access -
        # since some lighter-weight orchestrator test doubles only
        # implement the handful of methods a given test actually exercises
        # (get_workflow_run/resume_workflow/execute_agent), not the fuller
        # AgentOrchestrator surface. Missing either simply means this
        # faster memory-based path is unavailable - callers already fall
        # back to the official step status in that case.
        agent_registry = getattr(self._orchestrator, "agent_registry", None)
        memory_service = getattr(self._orchestrator, "memory_service", None)
        if agent_registry is None or memory_service is None:
            return None

        # Reads as genie-orchestrator's own identity - every
        # solution-discovery-workflow step (including build-solution) is
        # itself configured under this agent id (see
        # config/workflows/registry.yaml), and this is the exact same key
        # (the step id) and identity WorkflowStepExecutor._read_step_output
        # already uses to read a prior step's real output back out of
        # Shared Memory.
        requesting_agent = get_enabled_agent(agent_registry, "genie-orchestrator")
        records = await memory_service.shared.read(
            requesting_agent=requesting_agent,
            session_id=session_id,
            trace_id=trace_id,
            key=step_id,
        )
        if not records:
            return None
        output_text = records[0].content.get("output_text")
        return output_text if isinstance(output_text, str) and output_text else None

    async def _ensure_upstream_steps_completed(
        self, *, run: WorkflowRunResult, session_id: str, trace_id: str
    ) -> WorkflowRunResult:
        """Self-heals a workflow run that has not yet finished every step
        Deploy & Launch reads from (``build-solution``)
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
        required_step_ids = (self._build_step_id,)

        # The common case this self-heal exists for is a genuine race of a
        # few SECONDS - Workshop's "Proceed" is a client-side navigation
        # that does not wait for genie-orchestrator's own, slightly slower
        # official step-completion write to land. A single, instantaneous
        # check right as Deploy & Launch loads can lose that race even
        # though the real work is already finished or about to be -
        # unconditionally resuming (which discards the user's real
        # policies/excluded_agents, see below) in that situation needlessly
        # re-runs an already-approved build. Poll a few times with a short
        # delay before concluding the step is genuinely not done and
        # falling back to a real resume.
        for attempt in range(self._upstream_grace_check_attempts):
            if all(
                [await self._step_completed(run, step_id, trace_id=trace_id) for step_id in required_step_ids]
            ):
                return run
            if attempt < self._upstream_grace_check_attempts - 1:
                await asyncio.sleep(self._upstream_grace_check_interval_seconds)

        step_inputs: dict[str, WorkflowStepInput] = {}
        if not await self._step_completed(run, self._build_step_id, trace_id=trace_id):
            step_inputs[self._build_step_id] = WorkflowStepInput(
                step_id=self._build_step_id,
                variables={"policies": "", "excluded_agents": ""},
            )

        resumed = await self._orchestrator.resume_workflow(
            workflow_run_id=run.workflow_run_id,
            session_id=session_id,
            trace_id=trace_id,
            step_inputs=step_inputs or None,
        )

        # `resume_workflow` can legitimately return WITHOUT raising even when
        # a required step still isn't done - e.g. the run paused again on an
        # EARLIER stage's own `requires_human_proceed` gate (design-architecture
        # comes before build-solution; this self-heal only ever targets
        # build-solution, so it cannot clear a still-pending architecture
        # proceed) or on a governance approval checkpoint. Blindly trusting
        # this resume "worked" let `_execute_steps` reach a much later,
        # unrelated step (e.g. provision-foundry-agents) before failing with a
        # confusing "Workflow step 'build-solution' has not completed"
        # error - fail closed HERE instead, immediately and clearly, so the
        # very first pipeline step records an actionable message pointing at
        # the real blocker (the resumed run's own `status`/`detail`).
        resumed_completed = [
            await self._step_completed(resumed, step_id, trace_id=trace_id) for step_id in required_step_ids
        ]
        if not all(resumed_completed):
            raise UnknownWorkflowRunError(
                "Deploy & Launch cannot start: the mission workflow is not fully "
                f"complete yet (status='{resumed.status}'"
                + (f", {resumed.detail}" if resumed.detail else "")
                + "). Go back to Workshop and proceed through any pending step "
                "before starting Deploy & Launch again."
            )

        return resumed

    async def _get_step_output(self, run: WorkflowRunResult, step_id: str, *, trace_id: str) -> str:
        step = next((r for r in run.step_results if r.step_id == step_id), None)
        if step is not None and step.status == "completed":
            return step.output_text or ""
        memory_output = await self._read_step_memory_output(
            session_id=run.session_id, trace_id=trace_id, step_id=step_id
        )
        if memory_output is not None:
            return memory_output
        raise UnknownWorkflowRunError(
            f"Workflow step '{step_id}' has not completed for run '{run.workflow_run_id}'."
        )

    async def _execute_steps(
        self,
        *,
        pipeline_run: DeploymentPipelineRun,
        run: WorkflowRunResult,
        mission_slug: str,
        backend_root: Path,
        frontend_root: Path,
        trace_id: str,
        resume_from_step: str | None = None,
    ) -> None:
        """Executes the deployment pipeline steps in order.
        
        If ``resume_from_step`` is provided, only executes from that step onward,
        skipping already-completed prior steps. All steps after the resume point
        are reset to "not-started" status.
        """
        orchestrator_foundry_name = "orchestrator"
        test_output_text = ""

        # Determine the starting index based on resume_from_step
        start_index = 0
        if resume_from_step:
            try:
                start_index = DEPLOYMENT_STEP_ORDER.index(resume_from_step)
                # Reset all steps from the resume point onward to "not-started"
                for step_id in DEPLOYMENT_STEP_ORDER[start_index:]:
                    step_result = self._step_result(pipeline_run, step_id)
                    step_result.status = "not-started"
                    step_result.error = None
                    step_result.detail = None
                    step_result.started_at = None
                    step_result.completed_at = None
            except ValueError:
                # Invalid step ID provided, start from beginning
                start_index = 0

        for step_id in DEPLOYMENT_STEP_ORDER[start_index:]:
            await self._publish(pipeline_run, step_id=step_id, event_type="step_started")
            step_result = self._step_result(pipeline_run, step_id)
            step_result.status = "running"
            step_result.started_at = datetime.now(UTC)

            try:
                if step_id == "generate-access-policy":
                    document = await self._access_policy_service.generate(mission_id=mission_slug)
                    pipeline_run.access_policy = document
                    detail = f"Generated least-access policy for {len(document.agents)} agent(s) with managed identity {document.mission_identity.identity_name if document.mission_identity else 'unknown'}."

                elif step_id == "provision-foundry-agents":
                    architecture_document = await self._get_step_output(
                        run, self._architecture_step_id, trace_id=trace_id
                    )
                    build_output_text = await self._get_step_output(
                        run, self._build_step_id, trace_id=trace_id
                    )
                    materialized = materialize_build(build_output_text)
                    agent_names = list(materialized.agent_modules.keys())
                    if materialized.orchestrator_module is not None:
                        agent_names.append("orchestrator")

                    # Mark every agent "running" before provisioning starts so
                    # the UI can show a real per-agent in-progress list while
                    # it is in flight, not just the step's own aggregate
                    # status. Agents are provisioned strictly one at a time
                    # (see MissionAgentProvisioningService.provision) - the
                    # callback below flips each one to "completed" the
                    # instant its own Foundry agent is created, so the list
                    # fills in live rather than jumping straight from "all
                    # running" to "all completed" at the very end.
                    pipeline_run.provisioned_agents = [
                        ProvisionedAgentStatus(agent_name=name, status="running")
                        for name in agent_names
                    ]
                    step_result.detail = f"Deploying agent 1 of {len(agent_names)} to Azure AI Foundry..."

                    async def _on_agent_provisioned(
                        record: ProvisionedMissionAgent,
                        *,
                        _step_result: DeploymentStepResult = step_result,
                        _agent_names: list[str] = agent_names,
                    ) -> None:
                        completed_so_far = 0
                        for index, agent in enumerate(pipeline_run.provisioned_agents):
                            if agent.agent_name == record.agent_name:
                                pipeline_run.provisioned_agents[index] = ProvisionedAgentStatus(
                                    agent_name=agent.agent_name,
                                    status="completed",
                                    foundry_agent_name=record.foundry_agent_name,
                                )
                            if pipeline_run.provisioned_agents[index].status == "completed":
                                completed_so_far += 1
                        if completed_so_far < len(_agent_names):
                            _step_result.detail = (
                                f"Deploying agent {completed_so_far + 1} of {len(_agent_names)} "
                                "to Azure AI Foundry..."
                            )

                    provisioned = await self._mission_agent_provisioning_service.provision(
                        mission_slug=mission_slug,
                        agent_names=agent_names,
                        architecture_document=architecture_document,
                        on_agent_provisioned=_on_agent_provisioned,
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

                    async def _on_backend_progress(
                        message: str, *, _step_result: DeploymentStepResult = step_result
                    ) -> None:
                        _step_result.detail = message

                    mission_identity_resource_id = (
                        pipeline_run.access_policy.mission_identity.identity_resource_id
                        if pipeline_run.access_policy and pipeline_run.access_policy.mission_identity
                        else None
                    )
                    backend_result = await self._backend_deployment_service.deploy(
                        mission_slug=mission_slug,
                        build_root=backend_root,
                        mission_identity_resource_id=mission_identity_resource_id,
                        on_progress=_on_backend_progress,
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
                    (frontend_root / "index.html").write_text(
                        _FRONTEND_INDEX_HTML_TEMPLATE, encoding="utf-8"
                    )
                    (frontend_root / "package.json").write_text(
                        _FRONTEND_PACKAGE_JSON, encoding="utf-8"
                    )
                    (frontend_root / "tsconfig.json").write_text(
                        _FRONTEND_TSCONFIG_JSON, encoding="utf-8"
                    )
                    (frontend_root / "vite.config.ts").write_text(
                        _FRONTEND_VITE_CONFIG, encoding="utf-8"
                    )
                    src_root = frontend_root / "src"
                    src_root.mkdir(parents=True, exist_ok=True)
                    (src_root / "main.tsx").write_text(_FRONTEND_MAIN_TSX, encoding="utf-8")
                    (src_root / "env.d.ts").write_text(_FRONTEND_ENV_D_TS, encoding="utf-8")
                    public_root = frontend_root / "public"
                    public_root.mkdir(parents=True, exist_ok=True)
                    (public_root / "runtime-config.js").write_text(
                        f'window.__MISSION_BACKEND_URL__ = "{pipeline_run.backend_url}";\n',
                        encoding="utf-8",
                    )
                    detail = f"Frontend wired to real backend URL {pipeline_run.backend_url}."

                elif step_id == "deploy-frontend-app":

                    async def _on_frontend_progress(
                        message: str, *, _step_result: DeploymentStepResult = step_result
                    ) -> None:
                        _step_result.detail = message

                    frontend_result = await self._frontend_deployment_service.deploy(
                        mission_slug=mission_slug,
                        ui_root=frontend_root,
                        on_progress=_on_frontend_progress,
                    )
                    pipeline_run.frontend_url = frontend_result.frontend_url
                    detail = f"Frontend deployed at {frontend_result.frontend_url}."

                elif step_id == "generate-test-suite":
                    # Generated for real, right here, against the real
                    # deployed build (not read back from an upstream
                    # workflow step) - Deploy & Launch is deliberately NOT
                    # a workflow step (see module docstring), so this calls
                    # the Test Generation Agent directly via
                    # AgentOrchestrator.execute_agent, the same
                    # outside-any-workflow-step execution path already used
                    # by Workshop's per-component "Regenerate" action.
                    build_output_text = await self._get_step_output(
                        run, self._build_step_id, trace_id=trace_id
                    )
                    requirements_text = await self._get_step_output(
                        run, self._requirements_step_id, trace_id=trace_id
                    )
                    generation_result = await self._orchestrator.execute_agent(
                        agent_id="test-generation-agent",
                        prompt_id="test-generation-v1",
                        variables={
                            "artifact": build_output_text,
                            "requirements": requirements_text,
                            "user_message": "",
                        },
                        session_id=pipeline_run.session_id,
                        trace_id=pipeline_run.id,
                    )
                    test_output_text = generation_result.output_text
                    modules = extract_test_modules(test_output_text)
                    if not has_pytest_discoverable_tests(modules):
                        correction_result = await self._orchestrator.execute_agent(
                            agent_id="test-generation-agent",
                            prompt_id="test-generation-v1",
                            variables={
                                "artifact": build_output_text,
                                "requirements": requirements_text,
                                "user_message": (
                                    "Your prior response contained no pytest-discoverable Python test. "
                                    "Return one or more fenced python blocks containing module-level "
                                    "test_<name> functions with real assertions."
                                ),
                            },
                            session_id=pipeline_run.session_id,
                            trace_id=pipeline_run.id,
                        )
                        test_output_text = correction_result.output_text
                        modules = extract_test_modules(test_output_text)
                    if not has_pytest_discoverable_tests(modules):
                        raise DeploymentPipelineStepFailedError(
                            "Test Generation Agent did not produce a pytest-discoverable test function "
                            "after a corrective retry."
                        )
                    detail = f"Generated {len(modules)} test module(s) against the deployed build."

                elif step_id == "execute-test-suite":
                    modules = extract_test_modules(test_output_text)
                    step_result.detail = (
                        f"Running {len(modules)} generated test module(s) with pytest against the "
                        "deployed backend build (up to 2 minutes)..."
                    )
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
                    step_result.detail = "Scanning the deployed backend build's dependencies and code for vulnerabilities..."
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
    mission_identity_service: MissionIdentityService | NullMissionIdentityService,
    mission_agent_provisioning_service: MissionAgentProvisioningService
    | NullMissionAgentProvisioningService,
    backend_deployment_service: BackendDeploymentService | NullBackendDeploymentService,
    frontend_deployment_service: (
        ContainerAppFrontendDeploymentService | NullContainerAppFrontendDeploymentService
    ),
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
        mission_identity_service=mission_identity_service,
        mission_agent_provisioning_service=mission_agent_provisioning_service,
        backend_deployment_service=backend_deployment_service,
        frontend_deployment_service=frontend_deployment_service,
        test_execution_service=TestExecutionService(),
        security_scan_service=SecurityScanService(),
        build_workspace_root=settings.deployment_build_workspace_root,
    )
