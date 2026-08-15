"""Debugging API routes.

Thin wrapper over ``AgentOrchestrator.handle_failure`` - lets an operator
(or a future automated monitor) manually trigger the same
``FailureDetected`` -> debugging-workflow path Phase 6's runtime already
invokes automatically. Debugging agents execute exactly like every other
agent (Azure-hosted, via ``AzureAgentGateway`` in production) - this route
performs no diagnosis itself.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import get_agent_orchestrator, get_session_service
from app.models.workflow_models import WorkflowRunResult
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.security.auth_models import AuthenticatedUser
from app.security.dependencies import get_current_user
from app.services.session_service import SessionService

router = APIRouter(prefix="/sessions/{session_id}/debugging", tags=["debugging"])


class HandleFailureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_agent_id: str = Field(min_length=1)
    error: str = Field(min_length=1)
    trace_id: str | None = None


@router.post("/handle-failure")
async def handle_failure(
    session_id: str,
    body: HandleFailureRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
    orchestrator: AgentOrchestrator = Depends(get_agent_orchestrator),
) -> WorkflowRunResult:
    await session_service.get_session(session_id=session_id, requesting_user_id=user.user_id)
    return await orchestrator.handle_failure(
        session_id=session_id,
        source_agent_id=body.source_agent_id,
        error=body.error,
        trace_id=body.trace_id,
    )
