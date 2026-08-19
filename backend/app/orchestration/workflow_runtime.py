"""Workflow runtime: the core agentic collaboration engine.

Implements "PARALLEL EXECUTION" and drives the "WORKFLOW STATE MACHINE"
for Phase 6. Computes sequential/parallel execution waves from each
``WorkflowStep.depends_on``, executes every wave (steps within a wave run
concurrently) via ``WorkflowStepExecutor``, records synchronization
checkpoints, handoffs, and collaboration events, and enforces two
independent, optional per-step gates: ``requires_approval_checkpoint``
(a governance ApprovalRequest must be approved - see
``_enforce_approval_gate``) and ``requires_human_proceed`` (a plain
structural pause with no ApprovalService involved at all - see
``_enforce_human_proceed_gate``, used by Genie's own Requirements ->
Architecture -> Code mission stages). Contains no agent reasoning: every
step's actual execution is delegated to ``WorkflowStepExecutor`` ->
``AgentGateway`` (``AzureAgentGateway`` in production). A step runs exactly
once per call - any ``FoundryUnavailableError`` (a real outage, a content
fidelity rejection, ...) surfaces immediately as a "failed" step rather
than silently retrying in a loop, and remains eligible for the existing
MANUAL retry path (a caller resuming this same ``workflow_run_id``) - see
``_execute_step_with_automatic_retries``/``_MAX_STEP_RETRIES``.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import uuid4

from app.agents.foundry.errors import FoundryUnavailableError
from app.governance.approval_service import ApprovalService
from app.models.workflow_models import WorkflowRunResult, WorkflowStepInput, WorkflowStepResult
from app.orchestration.collaboration_service import CollaborationService
from app.orchestration.handoff_service import HandoffService
from app.orchestration.workflow_checkpoint_service import WorkflowCheckpointService
from app.orchestration.workflow_state_machine import WorkflowStateMachine
from app.orchestration.workflow_step_executor import WorkflowStepExecutor
from app.workflows.models import WorkflowStep
from app.workflows.registry import WorkflowRegistry

__all__ = ["ApprovalCapabilityMissingError", "UnknownWorkflowError", "WorkflowRuntime"]

# Kept at 0 (no automatic retries): a step that fails - including a
# retryable-shaped ``FoundryUnavailableError`` (real outage, content
# fidelity rejection, ...) - surfaces immediately as a genuinely "failed"
# step instead of silently re-running the whole step (and its delegated
# specialist call) several times in a row, which previously looked like
# the mission was "stuck looping" with no feedback for minutes. The
# failure remains eligible for the existing MANUAL retry path
# (resume_workflow), which lets a human decide whether to retry at all.
_MAX_STEP_RETRIES = 0


class UnknownWorkflowError(RuntimeError):
    """Raised when a workflow run references a workflow id missing from the registry."""


class ApprovalCapabilityMissingError(RuntimeError):
    """Raised when a step requires an approval checkpoint but no ApprovalService is wired."""


def _compute_waves(steps: list[WorkflowStep]) -> list[list[WorkflowStep]]:
    """Group steps into sequential waves; steps within a wave may run in parallel."""

    remaining = {step.id: step for step in steps}
    finished: set[str] = set()
    waves: list[list[WorkflowStep]] = []

    while remaining:
        wave = [s for s in remaining.values() if all(dep in finished for dep in s.depends_on)]
        if not wave:
            raise UnknownWorkflowError(
                "Cannot compute execution order: a dependency cycle exists among "
                f"steps {sorted(remaining)}."
            )
        waves.append(wave)
        for step in wave:
            finished.add(step.id)
            del remaining[step.id]

    return waves


class WorkflowRuntime:
    """Coordinates independently deployed Azure AI Foundry agents through a workflow run."""

    def __init__(
        self,
        *,
        workflow_registry: WorkflowRegistry,
        step_executor: WorkflowStepExecutor,
        handoff_service: HandoffService,
        collaboration_service: CollaborationService,
        checkpoint_service: WorkflowCheckpointService,
        approval_service: ApprovalService | None = None,
    ) -> None:
        self._workflow_registry = workflow_registry
        self._step_executor = step_executor
        self._handoff_service = handoff_service
        self._collaboration_service = collaboration_service
        self._checkpoint_service = checkpoint_service
        self._approval_service = approval_service

    async def run_workflow(
        self,
        *,
        workflow_id: str,
        session_id: str,
        trace_id: str,
        step_inputs: dict[str, WorkflowStepInput] | None = None,
        transcript_text: str = "",
        resume_from: WorkflowRunResult | None = None,
        agent_scope_id: str | None = None,
        workflow_run_id: str | None = None,
        on_progress: Callable[[WorkflowRunResult], Awaitable[None]] | None = None,
    ) -> WorkflowRunResult:
        """Runs every wave of ``workflow_id`` until it completes, pauses for an
        approval checkpoint, or a step fails.

        ``on_progress``, if supplied, is awaited with a still-``"running"``
        ``WorkflowRunResult`` snapshot after every wave that finishes
        successfully (before the next wave starts) - so a caller can
        durably persist each stage's result as it lands, instead of only
        learning the final outcome once this whole (potentially
        multi-minute, multi-wave) call returns. Without this, a backend
        process restart (or any interruption) mid-run would silently lose
        every already-completed wave's real output along with it.
        """
        try:
            workflow = self._workflow_registry.get(workflow_id)
        except KeyError as exc:
            raise UnknownWorkflowError(f"Unknown workflow id '{workflow_id}'.") from exc

        # A caller may pre-allocate the run id (see
        # ``WorkflowExecutionService.start_workflow_background``) so the run
        # is addressable via ``GET /runs/{id}`` the instant it is accepted,
        # rather than only once this whole multi-minute call returns.
        if resume_from is not None:
            workflow_run_id = resume_from.workflow_run_id
        elif workflow_run_id is None:
            workflow_run_id = str(uuid4())
        # A resumed run always keeps its original dedicated-agent-fleet scope
        # (e.g. a requirement group id), even if the caller (a customer chat/
        # reanalyze interaction) does not re-supply it - so follow-up
        # interactions on an already-scoped run keep reaching the same fleet.
        effective_scope_id = resume_from.agent_scope_id if resume_from else agent_scope_id
        step_by_id = {step.id: step for step in workflow.steps}
        inputs_by_id = step_inputs or {}
        waves = _compute_waves(workflow.steps)

        step_results: list[WorkflowStepResult] = list(resume_from.step_results) if resume_from else []
        # Only a genuinely-completed prior step counts as done - a
        # previously FAILED step (see the retryable "failed" WorkflowRunResult
        # returned below) must NOT be treated as completed here, or resuming
        # that same run would silently skip retrying it forever.
        completed_ids: set[str] = {
            result.step_id for result in step_results if result.status == "completed"
        }

        state_machine = WorkflowStateMachine.start(
            workflow_run_id=workflow_run_id, workflow_id=workflow_id, session_id=session_id
        )
        state_machine.transition("running")

        for wave_index, wave in enumerate(waves):
            # A step already completed in resume_from is normally skipped -
            # resume_workflow only advances a paused run. The one exception:
            # a step explicitly named in step_inputs is re-executed even if
            # already completed, so a customer/user chat message ("interact
            # with the agents" against an already-completed run) actually
            # reaches that agent instead of being silently ignored.
            pending_steps = [
                step
                for step in wave
                if step.id not in completed_ids or step.id in inputs_by_id
            ]
            if not pending_steps:
                continue

            proceed_gate_result = self._enforce_human_proceed_gate(
                pending_steps,
                workflow_run_id=workflow_run_id,
                workflow_id=workflow_id,
                session_id=session_id,
                waves=waves,
                step_results=step_results,
                state_machine=state_machine,
                inputs_by_id=inputs_by_id,
                agent_scope_id=effective_scope_id,
            )
            if proceed_gate_result is not None:
                return proceed_gate_result

            gate_result = await self._enforce_approval_gate(
                pending_steps,
                workflow_run_id=workflow_run_id,
                workflow_id=workflow_id,
                session_id=session_id,
                trace_id=trace_id,
                waves=waves,
                step_results=step_results,
                state_machine=state_machine,
                agent_scope_id=effective_scope_id,
            )
            if gate_result is not None:
                return gate_result

            step_outputs = {result.step_id: result.output_text or "" for result in step_results}
            previous_variables_by_id = {
                result.step_id: result.resolved_variables for result in step_results
            }
            # ``return_exceptions=True`` so one step's transient agent-execution
            # failure (``FoundryUnavailableError`` - a model hallucination, a
            # malformed tool-call, a real Foundry outage, ...) never discards
            # the OTHER already-completed steps in this same call/wave, and so
            # this run can still be stored below as a retryable "failed" result
            # instead of an unhandled exception silently losing every earlier
            # wave's real output (see "Approval checkpoint gating pattern" /
            # deploy-backend.md session notes for the prior incarnation of this
            # exact class of bug). Any OTHER exception type (unknown agent,
            # missing memory reference, governance write failure, ...)
            # represents a genuine configuration/system-integrity fault, not a
            # retryable agent hiccup, and must keep failing closed by
            # propagating immediately - never silently downgraded to a
            # "failed" step result.
            raw_results = await asyncio.gather(
                *(
                    self._execute_step_with_automatic_retries(
                        step=step,
                        session_id=session_id,
                        trace_id=trace_id,
                        correlation_id=f"{workflow_run_id}:{step.id}",
                        step_input=inputs_by_id.get(step.id),
                        transcript_text=transcript_text,
                        step_outputs=step_outputs,
                        step_variables=previous_variables_by_id,
                        previous_variables=previous_variables_by_id.get(step.id),
                        agent_scope_id=effective_scope_id,
                        workflow_run_id=workflow_run_id,
                    )
                    for step in pending_steps
                ),
                return_exceptions=True,
            )

            wave_results: list[WorkflowStepResult] = []
            step_failed = False
            for step, outcome in zip(pending_steps, raw_results, strict=True):
                if isinstance(outcome, BaseException):
                    if not isinstance(outcome, FoundryUnavailableError):
                        state_machine.transition("failed", detail=str(outcome))
                        raise outcome
                    step_failed = True
                    now = datetime.now(UTC)
                    wave_results.append(
                        WorkflowStepResult(
                            step_id=step.id,
                            agent_id=step.agent_id,
                            status="failed",
                            error=str(outcome),
                            started_at=now,
                            completed_at=now,
                        )
                    )
                else:
                    wave_results.append(outcome)

            # Drop any earlier result (completed OR failed) for every step id
            # about to be (re)executed in this wave - covers both the
            # existing "explicit step_inputs override an already-completed
            # step" case and the new "retry a previously-failed step" case
            # (see completed_ids.update below: a failed step's id is never
            # added to completed_ids, so it naturally reappears in
            # pending_steps on the next resume_workflow call).
            pending_ids = {step.id for step in pending_steps}
            step_results = [result for result in step_results if result.step_id not in pending_ids]
            step_results.extend(wave_results)
            # Only genuinely-completed steps count as "completed" - a failed
            # step's id is deliberately left out of completed_ids so that a
            # later resume_workflow call for this same workflow_run_id (see
            # ``resume_from`` above) naturally re-attempts exactly this step
            # again as a still-pending one, instead of skipping it forever.
            completed_ids.update(
                result.step_id for result in wave_results if result.status == "completed"
            )

            if step_failed:
                # Store (never discard) whatever earlier waves already
                # completed in this same call, plus this wave's failed step
                # result, as a genuinely retryable "failed" run - a caller
                # can resume_workflow the same workflow_run_id to re-attempt
                # exactly this still-pending step (and any steps after it)
                # without losing prior progress or having to restart the
                # whole mission from scratch.
                state_machine.transition("failed", detail="One or more steps failed.")
                return WorkflowRunResult(
                    workflow_run_id=workflow_run_id,
                    workflow_id=workflow_id,
                    session_id=session_id,
                    status="failed",
                    waves=[[step.id for step in wave] for wave in waves],
                    step_results=step_results,
                    agent_scope_id=effective_scope_id,
                )

            await self._record_handoffs(
                pending_steps,
                step_by_id=step_by_id,
                session_id=session_id,
                workflow_run_id=workflow_run_id,
                trace_id=trace_id,
            )
            self._record_parallel_collaboration(
                pending_steps,
                wave_index=wave_index,
                session_id=session_id,
                workflow_run_id=workflow_run_id,
                trace_id=trace_id,
            )
            self._checkpoint_service.record_checkpoint(
                workflow_run_id=workflow_run_id,
                session_id=session_id,
                wave_index=wave_index,
                completed_step_ids=sorted(completed_ids),
            )

            if on_progress is not None:
                await on_progress(
                    WorkflowRunResult(
                        workflow_run_id=workflow_run_id,
                        workflow_id=workflow_id,
                        session_id=session_id,
                        status="running",
                        waves=[[step.id for step in wave] for wave in waves],
                        step_results=step_results,
                        agent_scope_id=effective_scope_id,
                    )
                )

        state_machine.transition("completed")
        return WorkflowRunResult(
            workflow_run_id=workflow_run_id,
            workflow_id=workflow_id,
            session_id=session_id,
            status="completed",
            waves=[[step.id for step in wave] for wave in waves],
            step_results=step_results,
            agent_scope_id=effective_scope_id,
        )

    async def _execute_step_with_automatic_retries(
        self,
        *,
        step: WorkflowStep,
        session_id: str,
        trace_id: str,
        correlation_id: str,
        step_input: WorkflowStepInput | None,
        transcript_text: str,
        step_outputs: dict[str, str],
        step_variables: dict[str, dict[str, str]],
        previous_variables: dict[str, str] | None,
        agent_scope_id: str | None,
        workflow_run_id: str,
    ) -> WorkflowStepResult:
        """Executes ``step``, automatically retrying up to ``_MAX_STEP_RETRIES``
        additional times (currently 0 - see that constant) when it fails
        with a retryable-shaped ``FoundryUnavailableError``.

        With ``_MAX_STEP_RETRIES`` at 0 this is a single attempt: any
        ``FoundryUnavailableError`` propagates immediately and is stored as
        a "failed" step eligible for the existing MANUAL retry path
        (resume_workflow) rather than being silently retried in place. Any
        OTHER exception type was never retried either - it propagates on
        the very first attempt exactly as it did before automatic retries
        existed.
        """

        for attempt in range(1, _MAX_STEP_RETRIES + 2):
            try:
                return await self._step_executor.execute_step(
                    step=step,
                    session_id=session_id,
                    trace_id=trace_id,
                    correlation_id=correlation_id,
                    step_input=step_input,
                    transcript_text=transcript_text,
                    step_outputs=step_outputs,
                    step_variables=step_variables,
                    previous_variables=previous_variables,
                    agent_scope_id=agent_scope_id,
                    workflow_run_id=workflow_run_id,
                )
            except FoundryUnavailableError:
                if attempt > _MAX_STEP_RETRIES:
                    raise
        raise AssertionError("unreachable: loop above always returns or raises")

    def _enforce_human_proceed_gate(
        self,
        pending_steps: list[WorkflowStep],
        *,
        workflow_run_id: str,
        workflow_id: str,
        session_id: str,
        waves: list[list[WorkflowStep]],
        step_results: list[WorkflowStepResult],
        state_machine: WorkflowStateMachine,
        inputs_by_id: dict[str, WorkflowStepInput],
        agent_scope_id: str | None = None,
    ) -> WorkflowRunResult | None:
        """Pauses before any step flagged ``requires_human_proceed`` until the
        caller's own ``step_inputs`` explicitly names that step - i.e. the
        human clicked a 'Proceed to <next stage>' action naming this exact
        step. Unlike ``_enforce_approval_gate``, this never touches
        ApprovalService: it is a plain structural pause, not a governance
        approval decision, so there is nothing to request/decide/reject -
        the very next call that explicitly targets this step's id is what
        clears it.
        """

        for step in pending_steps:
            if not step.requires_human_proceed or step.id in inputs_by_id:
                continue

            state_machine.transition(
                "waiting_for_proceed",
                detail=f"Waiting for the human to proceed to step '{step.id}'.",
            )
            return WorkflowRunResult(
                workflow_run_id=workflow_run_id,
                workflow_id=workflow_id,
                session_id=session_id,
                status="waiting_for_proceed",
                waves=[[s.id for s in wave] for wave in waves],
                step_results=step_results,
                detail=f"Waiting for the human to proceed to step '{step.id}'.",
                agent_scope_id=agent_scope_id,
            )

        return None

    async def _enforce_approval_gate(
        self,
        pending_steps: list[WorkflowStep],
        *,
        workflow_run_id: str,
        workflow_id: str,
        session_id: str,
        trace_id: str,
        waves: list[list[WorkflowStep]],
        step_results: list[WorkflowStepResult],
        state_machine: WorkflowStateMachine,
        agent_scope_id: str | None = None,
    ) -> WorkflowRunResult | None:
        """Returns a paused/blocked ``WorkflowRunResult`` if a gate is not satisfied, else None."""

        for step in pending_steps:
            checkpoint_id = step.requires_approval_checkpoint
            if checkpoint_id is None:
                continue

            if self._approval_service is None:
                state_machine.transition(
                    "failed",
                    detail=f"Step '{step.id}' requires approval checkpoint '{checkpoint_id}' "
                    f"but no ApprovalService is configured.",
                )
                raise ApprovalCapabilityMissingError(
                    f"Workflow step '{step.id}' requires approval checkpoint "
                    f"'{checkpoint_id}' but no ApprovalService is configured."
                )

            requests = await self._approval_service.list_requests_for_session(session_id)
            matching = [
                request
                for request in requests
                if request.checkpoint_id == checkpoint_id and request.subject_id == step.id
            ]
            if any(request.status == "approved" for request in matching):
                continue

            if any(request.status in ("rejected", "expired") for request in matching) and not any(
                request.status == "pending" for request in matching
            ):
                state_machine.transition(
                    "blocked",
                    detail=f"Approval checkpoint '{checkpoint_id}' for step '{step.id}' "
                    f"was not granted.",
                )
                return WorkflowRunResult(
                    workflow_run_id=workflow_run_id,
                    workflow_id=workflow_id,
                    session_id=session_id,
                    status="blocked",
                    waves=[[s.id for s in wave] for wave in waves],
                    step_results=step_results,
                    detail=f"Approval checkpoint '{checkpoint_id}' for step '{step.id}' "
                    f"was not granted.",
                    agent_scope_id=agent_scope_id,
                )

            if not matching:
                await self._approval_service.request_approval(
                    checkpoint_id=checkpoint_id,
                    session_id=session_id,
                    trace_id=trace_id,
                    requested_by_agent_id=step.agent_id,
                    subject_type="workflow_step",
                    subject_id=step.id,
                )

            state_machine.transition(
                "waiting_for_approval",
                detail=f"Waiting for approval checkpoint '{checkpoint_id}' on step '{step.id}'.",
            )
            return WorkflowRunResult(
                workflow_run_id=workflow_run_id,
                workflow_id=workflow_id,
                session_id=session_id,
                status="waiting_for_approval",
                waves=[[s.id for s in wave] for wave in waves],
                step_results=step_results,
                detail=f"Waiting for approval checkpoint '{checkpoint_id}' on step '{step.id}'.",
                agent_scope_id=agent_scope_id,
            )

        return None

    async def _record_handoffs(
        self,
        pending_steps: list[WorkflowStep],
        *,
        step_by_id: dict[str, WorkflowStep],
        session_id: str,
        workflow_run_id: str,
        trace_id: str,
    ) -> None:
        for step in pending_steps:
            for dependency_id in step.depends_on:
                dependency_step = step_by_id.get(dependency_id)
                if dependency_step is None or dependency_step.agent_id == step.agent_id:
                    continue
                await self._handoff_service.record_handoff(
                    source_agent_id=dependency_step.agent_id,
                    target_agent_id=step.agent_id,
                    session_id=session_id,
                    workflow_run_id=workflow_run_id,
                    trace_id=trace_id,
                    reason=(
                        f"Step '{step.id}' depends on completed step '{dependency_step.id}'."
                    ),
                    evidence_references=[f"workflow-step-output://{dependency_step.id}"],
                )

    def _record_parallel_collaboration(
        self,
        pending_steps: list[WorkflowStep],
        *,
        wave_index: int,
        session_id: str,
        workflow_run_id: str,
        trace_id: str,
    ) -> None:
        if len(pending_steps) < 2:
            return
        for index, step in enumerate(pending_steps):
            for other in pending_steps[index + 1 :]:
                self._collaboration_service.record_agent_to_agent(
                    session_id=session_id,
                    workflow_run_id=workflow_run_id,
                    trace_id=trace_id,
                    source_agent_id=step.agent_id,
                    target_agent_id=other.agent_id,
                    detail=f"Parallel execution in wave {wave_index}.",
                )
