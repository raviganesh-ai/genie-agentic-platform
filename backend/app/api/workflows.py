"""Workflow execution API routes.

Thin wrapper over ``AgentOrchestrator`` (Phase 6, unmodified): starts,
resumes, and looks up workflow runs for a session. Session ownership is
checked via ``SessionService`` before any orchestrator call.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import get_agent_orchestrator, get_session_service
from app.models.workflow_models import WorkflowRunResult
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.orchestration.workflow_execution_service import UnknownWorkflowRunError
from app.security.auth_models import AuthenticatedUser
from app.security.dependencies import get_current_user
from app.services.session_service import SessionService

router = APIRouter(prefix="/sessions/{session_id}/workflows", tags=["workflows"])


class RunWorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str | None = None


class ResumeWorkflowRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str | None = None


@router.post("/{workflow_id}/run")
async def run_workflow(
    session_id: str,
    workflow_id: str,
    body: RunWorkflowRequest | None = None,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
    orchestrator: AgentOrchestrator = Depends(get_agent_orchestrator),
) -> WorkflowRunResult:
    await session_service.get_session(session_id=session_id, requesting_user_id=user.user_id)
    trace_id = body.trace_id if body else None
    return await orchestrator.run_workflow(
        workflow_id=workflow_id, session_id=session_id, trace_id=trace_id
    )


@router.post("/runs/{workflow_run_id}/resume")
async def resume_workflow_run(
    session_id: str,
    workflow_run_id: str,
    body: ResumeWorkflowRunRequest | None = None,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
    orchestrator: AgentOrchestrator = Depends(get_agent_orchestrator),
) -> WorkflowRunResult:
    await session_service.get_session(session_id=session_id, requesting_user_id=user.user_id)
    trace_id = body.trace_id if body else None
    return await orchestrator.resume_workflow(
        workflow_run_id=workflow_run_id, session_id=session_id, trace_id=trace_id
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
    run = orchestrator.get_workflow_run(workflow_run_id)
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
    return orchestrator.list_workflow_runs(session_id)
