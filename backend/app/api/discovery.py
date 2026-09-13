"""Authenticated API routes for durable Discovery case lifecycle."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import get_discovery_service
from app.discovery.models import DiscoveryCase, DiscoveryQaMode
from app.discovery.service import DiscoveryService
from app.security.auth_models import AuthenticatedUser
from app.security.dependencies import get_current_user

router = APIRouter(tags=["discovery"])


class CreateDiscoveryCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_upload_ids: list[str] = Field(default_factory=list)
    model_deployment_ref: str | None = None
    save_enabled: bool = False


class SaveDiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class SelectPersonaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    persona_id: str | None = Field(default=None, min_length=1)
    persona_ids: list[str] = Field(default_factory=list)


class SetQaModeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: DiscoveryQaMode


class AnswerQuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str | None = None


class RecommendationConsentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accepted: bool


class SelectSolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    solution_id: str = Field(min_length=1)


@router.get("/discovery")
async def list_discovery_cases(
    user: AuthenticatedUser = Depends(get_current_user),
    discovery_service: DiscoveryService = Depends(get_discovery_service),
) -> list[DiscoveryCase]:
    return await discovery_service.list_cases(owner_user_id=user.user_id)


@router.post("/sessions/{session_id}/discovery", status_code=status.HTTP_201_CREATED)
async def create_or_resume_discovery_case(
    session_id: str,
    body: CreateDiscoveryCaseRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    discovery_service: DiscoveryService = Depends(get_discovery_service),
) -> DiscoveryCase:
    return await discovery_service.create_or_resume(
        session_id=session_id,
        requesting_user_id=user.user_id,
        source_upload_ids=body.source_upload_ids,
        model_deployment_ref=body.model_deployment_ref,
        save_enabled=body.save_enabled,
    )


@router.put("/sessions/{session_id}/discovery/save-preference")
async def set_discovery_save_preference(
    session_id: str,
    body: SaveDiscoveryRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    discovery_service: DiscoveryService = Depends(get_discovery_service),
) -> DiscoveryCase:
    return await discovery_service.set_save_preference(
        session_id=session_id,
        requesting_user_id=user.user_id,
        enabled=body.enabled,
    )


@router.get("/sessions/{session_id}/discovery")
async def get_discovery_case(
    session_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    discovery_service: DiscoveryService = Depends(get_discovery_service),
) -> DiscoveryCase:
    return await discovery_service.get_case(
        session_id=session_id,
        requesting_user_id=user.user_id,
    )


@router.post("/sessions/{session_id}/discovery/analyze")
async def analyze_discovery_personas(
    session_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    discovery_service: DiscoveryService = Depends(get_discovery_service),
) -> DiscoveryCase:
    return await discovery_service.analyze_personas(
        session_id=session_id, requesting_user_id=user.user_id
    )


@router.post("/sessions/{session_id}/discovery/persona")
async def select_discovery_persona(
    session_id: str,
    body: SelectPersonaRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    discovery_service: DiscoveryService = Depends(get_discovery_service),
) -> DiscoveryCase:
    selected_ids = body.persona_ids or ([body.persona_id] if body.persona_id else [])
    return await discovery_service.select_personas(
        session_id=session_id,
        requesting_user_id=user.user_id,
        persona_ids=selected_ids,
    )


@router.post("/sessions/{session_id}/discovery/qa-mode")
async def set_discovery_qa_mode(
    session_id: str,
    body: SetQaModeRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    discovery_service: DiscoveryService = Depends(get_discovery_service),
) -> DiscoveryCase:
    return await discovery_service.set_qa_mode(
        session_id=session_id,
        requesting_user_id=user.user_id,
        mode=body.mode,
    )


@router.post("/sessions/{session_id}/discovery/questions/{question_id}/answer")
async def answer_discovery_question(
    session_id: str,
    question_id: str,
    body: AnswerQuestionRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    discovery_service: DiscoveryService = Depends(get_discovery_service),
) -> DiscoveryCase:
    return await discovery_service.answer_question(
        session_id=session_id,
        requesting_user_id=user.user_id,
        question_id=question_id,
        answer=body.answer,
    )


@router.post("/sessions/{session_id}/discovery/questions/{question_id}/recommendation")
async def respond_to_discovery_recommendation(
    session_id: str,
    question_id: str,
    body: RecommendationConsentRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    discovery_service: DiscoveryService = Depends(get_discovery_service),
) -> DiscoveryCase:
    return await discovery_service.respond_to_recommendation(
        session_id=session_id,
        requesting_user_id=user.user_id,
        question_id=question_id,
        accepted=body.accepted,
    )


@router.post("/sessions/{session_id}/discovery/solutions")
async def generate_discovery_solutions(
    session_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    discovery_service: DiscoveryService = Depends(get_discovery_service),
) -> DiscoveryCase:
    return await discovery_service.generate_solutions(
        session_id=session_id, requesting_user_id=user.user_id
    )


@router.post("/sessions/{session_id}/discovery/solution")
async def select_discovery_solution(
    session_id: str,
    body: SelectSolutionRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    discovery_service: DiscoveryService = Depends(get_discovery_service),
) -> DiscoveryCase:
    return await discovery_service.select_solution(
        session_id=session_id,
        requesting_user_id=user.user_id,
        solution_id=body.solution_id,
    )


@router.post("/sessions/{session_id}/discovery/prototype")
async def start_discovery_prototype(
    session_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    discovery_service: DiscoveryService = Depends(get_discovery_service),
) -> DiscoveryCase:
    return await discovery_service.start_prototype(
        session_id=session_id, requesting_user_id=user.user_id
    )


@router.delete(
    "/sessions/{session_id}/discovery",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_discovery_case(
    session_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    discovery_service: DiscoveryService = Depends(get_discovery_service),
) -> Response:
    await discovery_service.delete_case(
        session_id=session_id,
        requesting_user_id=user.user_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)