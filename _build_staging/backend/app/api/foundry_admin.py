"""Foundry agent admin API routes (Phase 10A).

Thin, authenticated wrappers over the Foundry inventory/lifecycle/
synchronization services - operators use these to inspect the current
Foundry agent inventory, drift status, and lifecycle history, and to
trigger an on-demand synchronization run. Every route requires an
authenticated user with the ``foundry-admin`` role (least privilege - see
Security Requirements in ``.github/copilot-instructions.md``); denied
access is surfaced as a plain ``403`` before any service method runs.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import (
    get_agent_orchestrator,
    get_foundry_inventory_service,
    get_foundry_lifecycle_service,
    get_foundry_synchronization_service,
)
from app.models.agent_lifecycle_state import AgentLifecycleState, AgentLifecycleTransition
from app.models.drift_report import DriftReport
from app.models.foundry_agent_inventory_record import FoundryAgentInventoryRecord
from app.models.synchronization_result import SynchronizationReport
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.security.auth_models import AuthenticatedUser
from app.security.dependencies import get_current_user
from app.services.foundry_agent_inventory_service import FoundryAgentInventoryService
from app.services.foundry_agent_lifecycle_service import FoundryAgentLifecycleService
from app.services.foundry_agent_synchronization_service import FoundryAgentSynchronizationService

router = APIRouter(prefix="/api/admin/foundry", tags=["foundry-admin"])

_ADMIN_ROLE = "foundry-admin"


def _require_admin(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
    if _ADMIN_ROLE not in user.roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"User is not authorized for Foundry admin operations "
                f"(requires role '{_ADMIN_ROLE}')."
            ),
        )
    return user


class AgentLifecycleHistory(BaseModel):
    """Current lifecycle state and full transition history for one agent."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    current_state: AgentLifecycleState
    history: list[AgentLifecycleTransition]


@router.get("/inventory")
async def list_inventory(
    _: AuthenticatedUser = Depends(_require_admin),
    inventory_service: FoundryAgentInventoryService = Depends(get_foundry_inventory_service),
) -> list[FoundryAgentInventoryRecord]:
    return await inventory_service.list()


@router.get("/drift")
async def get_drift_reports(
    _: AuthenticatedUser = Depends(_require_admin),
    synchronization_service: FoundryAgentSynchronizationService
    | None = Depends(get_foundry_synchronization_service),
) -> dict[str, DriftReport]:
    if synchronization_service is None:
        return {}
    return synchronization_service.drift_reports()


@router.get("/synchronization-status")
async def get_synchronization_status(
    _: AuthenticatedUser = Depends(_require_admin),
    synchronization_service: FoundryAgentSynchronizationService
    | None = Depends(get_foundry_synchronization_service),
) -> SynchronizationReport | None:
    if synchronization_service is None:
        return None
    return synchronization_service.last_report()


@router.post("/synchronize")
async def trigger_synchronization(
    _: AuthenticatedUser = Depends(_require_admin),
    orchestrator: AgentOrchestrator = Depends(get_agent_orchestrator),
    synchronization_service: FoundryAgentSynchronizationService
    | None = Depends(get_foundry_synchronization_service),
) -> SynchronizationReport:
    if synchronization_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Azure AI Foundry is not configured; synchronization cannot run.",
        )
    return await synchronization_service.synchronize(orchestrator.agent_registry)


@router.get("/lifecycle/{agent_id}")
async def get_lifecycle_history(
    agent_id: str,
    _: AuthenticatedUser = Depends(_require_admin),
    lifecycle_service: FoundryAgentLifecycleService = Depends(get_foundry_lifecycle_service),
) -> AgentLifecycleHistory:
    current_state = await lifecycle_service.current_state(agent_id)
    history = await lifecycle_service.history(agent_id)
    return AgentLifecycleHistory(agent_id=agent_id, current_state=current_state, history=history)
