"""Memory API routes.

Exposes read-only access to Shared Collaboration Memory for a session (the
tier the Requirement Discovery Map renders): Personal Agent Memory is
intentionally not exposed here (it is readable only by its owning agent,
never by a human/UI caller, per the Memory Architecture in
``.github/copilot-instructions.md``); Enterprise Knowledge Memory promotion
is a reviewer/governance workflow, not a plain read API.

Reads are performed via a synthetic UI-viewer identity so they flow through
the identical policy and governance path every agent's memory read does.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from app.agents.models import AgentDefinition
from app.api.dependencies import get_memory_service, get_session_service
from app.memory.memory_models import SharedMemoryRecord
from app.memory.memory_service import MemoryService
from app.security.auth_models import AuthenticatedUser
from app.security.dependencies import get_current_user
from app.services.session_service import SessionService

router = APIRouter(prefix="/sessions/{session_id}/memory", tags=["memory"])


class MemoryReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str


def _ui_agent(user_id: str) -> AgentDefinition:
    return AgentDefinition(
        id=f"ui-viewer:{user_id}",
        name="Memory Viewer",
        role="ui_viewer",
        description="Synthetic identity for authenticated UI read-only memory queries.",
        memory_access=["shared"],
        enabled=True,
    )


@router.get("/shared")
async def list_shared_memory(
    session_id: str,
    trace_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
    memory_service: MemoryService = Depends(get_memory_service),
) -> list[SharedMemoryRecord]:
    await session_service.get_session(session_id=session_id, requesting_user_id=user.user_id)
    return await memory_service.shared.read(
        requesting_agent=_ui_agent(user.user_id), session_id=session_id, trace_id=trace_id
    )


@router.get("/shared/{key}")
async def get_shared_memory(
    session_id: str,
    key: str,
    trace_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
    memory_service: MemoryService = Depends(get_memory_service),
) -> list[SharedMemoryRecord]:
    await session_service.get_session(session_id=session_id, requesting_user_id=user.user_id)
    return await memory_service.shared.read(
        requesting_agent=_ui_agent(user.user_id),
        session_id=session_id,
        trace_id=trace_id,
        key=key,
    )
