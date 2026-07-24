"""Workflow runtime: the core agentic collaboration engine.

Implements "PARALLEL EXECUTION" and drives the "WORKFLOW STATE MACHINE"
for Phase 6. Computes sequential/parallel execution waves from each
``WorkflowStep.depends_on``, executes every wave (steps within a wave run
concurrently) via ``WorkflowStepExecutor``, records synchronization
checkpoints, handoffs, and collaboration events, and enforces approval
checkpoints. Contains no agent reasoning: every step's actual execution is
delegated to ``WorkflowStepExecutor`` -> ``AgentGateway``
(``AzureAgentGateway`` in production).
"""
from __future__ import annotations

import asyncio
from uuid import uuid4

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
    ) -> WorkflowRunResult:
        try:
            workflow = self._workflow_registry.get(workflow_id)
        except KeyError as exc:
            raise UnknownWorkflowError(f"Unknown workflow id '{workflow_id}'.") from exc

        workflow_run_id = resume_from.workflow_run_id if resume_from else str(uuid4())
        step_by_id = {step.id: step for step in workflow.steps}
        inputs_by_id = step_inputs or {}
        waves = _compute_waves(workflow.steps)

        step_results: list[WorkflowStepResult] = list(resume_from.step_results) if resume_from else []
        completed_ids: set[str] = {result.step_id for result in step_results}

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

            gate_result = await self._enforce_approval_gate(
                pending_steps,
                workflow_run_id=workflow_run_id,
                workflow_id=workflow_id,
                session_id=session_id,
                trace_id=trace_id,
                waves=waves,
                step_results=step_results,
                state_machine=state_machine,
            )
            if gate_result is not None:
                return gate_result

            try:
                step_outputs = {
                    result.step_id: result.output_text or "" for result in step_results
                }
                wave_results = await asyncio.gather(
                    *(
                        self._step_executor.execute_step(
                            step=step,
                            session_id=session_id,
                            trace_id=trace_id,
                            correlation_id=f"{workflow_run_id}:{step.id}",
                            step_input=inputs_by_id.get(step.id),
                            transcript_text=transcript_text,
                            step_outputs=step_outputs,
                        )
                        for step in pending_steps
                    )
                )
            except Exception as exc:
                state_machine.transition("failed", detail=str(exc))
                raise

            rerun_ids = {step.id for step in pending_steps if step.id in completed_ids}
            if rerun_ids:
                step_results = [result for result in step_results if result.step_id not in rerun_ids]
            step_results.extend(wave_results)
            completed_ids.update(result.step_id for result in wave_results)

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

        state_machine.transition("completed")
        return WorkflowRunResult(
            workflow_run_id=workflow_run_id,
            workflow_id=workflow_id,
            session_id=session_id,
            status="completed",
            waves=[[step.id for step in wave] for wave in waves],
            step_results=step_results,
        )

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
