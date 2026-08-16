"""Workflow execution API routes.

Thin wrapper over ``AgentOrchestrator`` (Phase 6, unmodified): starts,
resumes, and looks up workflow runs for a session. Session ownership is
checked via ``SessionService`` before any orchestrator call. A run request
never requires the caller to paste transcript text by hand: the session's
combined uploaded call transcript/recording text (``SessionService.
get_combined_transcript_text``) is always looked up and passed through as
``transcript_text``, which ``WorkflowStep.variable_sources``-configured
steps resolve automatically; ``step_inputs`` remains available for callers
that want to explicitly override or supply additional per-step variables.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import (
    get_agent_orchestrator,
    get_model_catalog_service,
    get_session_service,
)
from app.models.workflow_models import WorkflowRunResult, WorkflowStepInput
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.orchestration.workflow_execution_service import UnknownWorkflowRunError
from app.security.auth_models import AuthenticatedUser
from app.security.dependencies import get_current_user
from app.services.model_catalog_service import ModelCatalogService
from app.services.session_service import SessionService

router = APIRouter(prefix="/sessions/{session_id}/workflows", tags=["workflows"])


class RunWorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str | None = None
    step_inputs: dict[str, WorkflowStepInput] | None = None
    model_deployment_ref: str | None = None


class ResumeWorkflowRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str | None = None
    step_inputs: dict[str, WorkflowStepInput] | None = None
    model_deployment_ref: str | None = None


@router.post("/{workflow_id}/run", status_code=202)
async def run_workflow(
    session_id: str,
    workflow_id: str,
    body: RunWorkflowRequest | None = None,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
    model_catalog_service: ModelCatalogService = Depends(get_model_catalog_service),
    orchestrator: AgentOrchestrator = Depends(get_agent_orchestrator),
) -> WorkflowRunResult:
    """Accepts a workflow run and returns immediately with a ``running`` snapshot.

    A mission routinely runs far longer than the platform's HTTP request
    cap, so this never waits for completion. Track the run via the live
    workflow event stream or ``GET /runs/{workflow_run_id}``.
    """
    await session_service.get_session(session_id=session_id, requesting_user_id=user.user_id)
    transcript_text = await session_service.get_combined_transcript_text(
        session_id=session_id, requesting_user_id=user.user_id
    )
    trace_id = body.trace_id if body else None
    step_inputs = body.step_inputs if body else None
    model_deployment_ref = body.model_deployment_ref.strip() if body and body.model_deployment_ref else None
    agent_scope_id = None
    if model_deployment_ref:
        await model_catalog_service.ensure_available(model_deployment_ref)
        agent_scope_id = f"model:{model_deployment_ref}"
        await orchestrator.provision_customer_agents(
            session_id=session_id,
            scope_id=agent_scope_id,
            trace_id=trace_id,
            model_deployment_ref=model_deployment_ref,
        )
    return await orchestrator.start_workflow_background(
        workflow_id=workflow_id,
        session_id=session_id,
        trace_id=trace_id,
        step_inputs=step_inputs,
        transcript_text=transcript_text,
        agent_scope_id=agent_scope_id,
    )


@router.post("/runs/{workflow_run_id}/resume", status_code=202)
async def resume_workflow_run(
    session_id: str,
    workflow_run_id: str,
    body: ResumeWorkflowRunRequest | None = None,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
    model_catalog_service: ModelCatalogService = Depends(get_model_catalog_service),
    orchestrator: AgentOrchestrator = Depends(get_agent_orchestrator),
) -> WorkflowRunResult:
    """Accepts a resume and returns immediately - see ``run_workflow``."""
    await session_service.get_session(session_id=session_id, requesting_user_id=user.user_id)
    transcript_text = await session_service.get_combined_transcript_text(
        session_id=session_id, requesting_user_id=user.user_id
    )
    trace_id = body.trace_id if body else None
    step_inputs = body.step_inputs if body else None
    model_deployment_ref = body.model_deployment_ref.strip() if body and body.model_deployment_ref else None
    if model_deployment_ref:
        await model_catalog_service.ensure_available(model_deployment_ref)
    return await orchestrator.resume_workflow_background(
        workflow_run_id=workflow_run_id,
        session_id=session_id,
        trace_id=trace_id,
        step_inputs=step_inputs,
        transcript_text=transcript_text,
    )


@router.get("/runs/{workflow_run_id}")
async def get_workflow_run(
    session_id: str,
    workflow_run_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
    orchestrator: AgentOrchestrator = Depends(get_agent_orchestrator),
) -> WorkflowRunResult:
    await session_service.get_session(session_id=session_id, requesting_user_id=user.user_id)
    run = await orchestrator.get_workflow_run(workflow_run_id)
    if run is None or run.session_id != session_id:
        raise UnknownWorkflowRunError(f"Unknown workflow run id '{workflow_run_id}'.")
    return run


@router.get("/runs")
async def list_workflow_runs(
    session_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
    orchestrator: AgentOrchestrator = Depends(get_agent_orchestrator),
) -> list[WorkflowRunResult]:
    await session_service.get_session(session_id=session_id, requesting_user_id=user.user_id)
    return await orchestrator.list_workflow_runs(session_id)
