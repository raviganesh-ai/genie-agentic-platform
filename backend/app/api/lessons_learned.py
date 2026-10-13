"""Admin visibility into Genie's own diagnosed-failure lessons-learned backlog.

Genie platform-change Phase F: a queryable list of prompt/config change
candidates the Debugging Agent itself identified while diagnosing a real
failure (see ``app.orchestration.debugging_workflow_service``), so a human
can review and apply them through the normal git/PR process instead of
relying on someone remembering to connect a diagnosis back to a prompt
change by hand.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import get_agent_orchestrator
from app.models.lessons_learned import LessonLearned
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.security.auth_models import AuthenticatedUser
from app.security.dependencies import require_genie_admin

router = APIRouter(prefix="/api/admin/lessons-learned", tags=["lessons-learned"])


@router.get("")
async def list_open_lessons_learned(
    _: AuthenticatedUser = Depends(require_genie_admin),
    orchestrator: AgentOrchestrator = Depends(get_agent_orchestrator),
) -> list[LessonLearned]:
    return await orchestrator.list_open_lessons_learned()
