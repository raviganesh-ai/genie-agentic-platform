"""Agent registry API routes.

Thin, read-only wrapper over the already-loaded ``AgentRegistry`` exposed
by ``AgentOrchestrator`` - lists registered agents (for the Agent Arena UI)
and looks up a single agent's definition. No agent execution happens here.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.agents.models import AgentDefinition
from app.api.dependencies import get_agent_orchestrator
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.security.auth_models import AuthenticatedUser
from app.security.dependencies import get_current_user

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("")
async def list_agents(
    user: AuthenticatedUser = Depends(get_current_user),
    orchestrator: AgentOrchestrator = Depends(get_agent_orchestrator),
) -> list[AgentDefinition]:
    return orchestrator.agent_registry.list()


@router.get("/{agent_id}")
async def get_agent(
    agent_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    orchestrator: AgentOrchestrator = Depends(get_agent_orchestrator),
) -> AgentDefinition:
    try:
        return orchestrator.agent_registry.get(agent_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown agent id '{agent_id}'."
        ) from exc
