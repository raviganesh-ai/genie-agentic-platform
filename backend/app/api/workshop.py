"""Workshop Experience API routes.

Thin wrapper over ``WorkshopService`` - chat with all/individual agents,
challenge a recommendation, request an alternative architecture, submit a
generic re-analysis request, and update priorities. Every action routes
through the unmodified Phase 6 ``AgentOrchestrator``.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import get_workshop_service
from app.models.reanalysis_models import ReanalysisRequestType, ReanalysisResult
from app.models.workflow_models import WorkflowRunResult
from app.security.auth_models import AuthenticatedUser
from app.security.dependencies import get_current_user
from app.services.workshop_service import WorkshopService

router = APIRouter(prefix="/sessions/{session_id}/workshop", tags=["workshop"])


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_run_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    trace_id: str | None = None


class ReanalysisActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_run_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    target_recommendation_id: str | None = None
    rationale: str = Field(default="")


class SubmitReanalysisRequest(ReanalysisActionRequest):
    request_type: ReanalysisRequestType


class RegenerateComponentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component_label: str = Field(min_length=1)
    existing_code: str = Field(min_length=1)
    instructions: str = Field(min_length=1)
    trace_id: str | None = None


class RegenerateComponentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component_label: str
    code: str


@router.post("/chat")
async def chat_with_all_agents(
    session_id: str,
    body: ChatRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    workshop_service: WorkshopService = Depends(get_workshop_service),
) -> WorkflowRunResult:
    return await workshop_service.chat_with_all_agents(
        session_id=session_id,
        requesting_user_id=user.user_id,
        workflow_run_id=body.workflow_run_id,
        message=body.message,
        trace_id=body.trace_id,
    )


@router.post("/chat/{agent_id}")
async def chat_with_agent(
    session_id: str,
    agent_id: str,
    body: ChatRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    workshop_service: WorkshopService = Depends(get_workshop_service),
) -> WorkflowRunResult:
    return await workshop_service.chat_with_agent(
        session_id=session_id,
        requesting_user_id=user.user_id,
        workflow_run_id=body.workflow_run_id,
        agent_id=agent_id,
        message=body.message,
        trace_id=body.trace_id,
    )


@router.post("/challenge")
async def challenge_recommendation(
    session_id: str,
    body: ReanalysisActionRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    workshop_service: WorkshopService = Depends(get_workshop_service),
) -> ReanalysisResult:
    return await workshop_service.challenge_recommendation(
        session_id=session_id,
        requesting_user_id=user.user_id,
        workflow_run_id=body.workflow_run_id,
        trace_id=body.trace_id,
        target_recommendation_id=body.target_recommendation_id,
        rationale=body.rationale,
    )


@router.post("/alternative")
async def request_alternative_architecture(
    session_id: str,
    body: ReanalysisActionRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    workshop_service: WorkshopService = Depends(get_workshop_service),
) -> ReanalysisResult:
    return await workshop_service.request_alternative_architecture(
        session_id=session_id,
        requesting_user_id=user.user_id,
        workflow_run_id=body.workflow_run_id,
        trace_id=body.trace_id,
        rationale=body.rationale,
    )


@router.post("/reanalysis")
async def submit_reanalysis_request(
    session_id: str,
    body: SubmitReanalysisRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    workshop_service: WorkshopService = Depends(get_workshop_service),
) -> ReanalysisResult:
    return await workshop_service.submit_reanalysis_request(
        session_id=session_id,
        requesting_user_id=user.user_id,
        workflow_run_id=body.workflow_run_id,
        trace_id=body.trace_id,
        request_type=body.request_type,
        target_recommendation_id=body.target_recommendation_id,
        rationale=body.rationale,
    )


@router.post("/priorities")
async def update_priorities(
    session_id: str,
    body: ReanalysisActionRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    workshop_service: WorkshopService = Depends(get_workshop_service),
) -> ReanalysisResult:
    return await workshop_service.update_priorities(
        session_id=session_id,
        requesting_user_id=user.user_id,
        workflow_run_id=body.workflow_run_id,
        trace_id=body.trace_id,
        rationale=body.rationale,
    )


@router.post("/regenerate-component")
async def regenerate_component(
    session_id: str,
    body: RegenerateComponentRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    workshop_service: WorkshopService = Depends(get_workshop_service),
) -> RegenerateComponentResponse:
    code = await workshop_service.regenerate_component(
        session_id=session_id,
        requesting_user_id=user.user_id,
        component_label=body.component_label,
        existing_code=body.existing_code,
        instructions=body.instructions,
        trace_id=body.trace_id,
    )
    return RegenerateComponentResponse(component_label=body.component_label, code=code)
