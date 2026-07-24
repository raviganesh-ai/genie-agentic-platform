"""Shared FastAPI dependency wiring for Phase 7 API routers.

Each dependency resolves a service constructed once at application startup
(see ``app.main.create_app``) and stored on ``app.state`` - this module
performs no business logic itself, keeping every API route thin per "API
routes must remain thin" in ``.github/copilot-instructions.md``.
"""
from __future__ import annotations

from fastapi import Request

from app.governance.approval_service import ApprovalService
from app.governance.governance_service import GovernanceService
from app.governance.replay_service import ReplayService
from app.governance.traceability_service import TraceabilityService
from app.memory.memory_service import MemoryService
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.security.cx_rate_limiter import CxRateLimiter
from app.security.cx_tokens import CxTokenService
from app.services.architecture_service import ArchitectureService
from app.services.foundry_agent_inventory_service import FoundryAgentInventoryService
from app.services.foundry_agent_lifecycle_service import FoundryAgentLifecycleService
from app.services.foundry_agent_synchronization_service import FoundryAgentSynchronizationService
from app.services.mission_control_service import MissionControlService
from app.services.output_service import OutputService
from app.services.session_service import SessionService
from app.services.starter_kit_service import StarterKitService
from app.services.workshop_service import WorkshopService
from app.transcription.speech_service import SpeechToTextService

__all__ = [
    "get_agent_orchestrator",
    "get_approval_service",
    "get_architecture_service",
    "get_cx_rate_limiter",
    "get_cx_token_service",
    "get_foundry_inventory_service",
    "get_foundry_lifecycle_service",
    "get_foundry_synchronization_service",
    "get_governance_service",
    "get_memory_service",
    "get_mission_control_service",
    "get_output_service",
    "get_replay_service",
    "get_session_service",
    "get_speech_to_text_service",
    "get_starter_kit_service",
    "get_traceability_service",
    "get_workshop_service",
]


def get_agent_orchestrator(request: Request) -> AgentOrchestrator:
    return request.app.state.agent_orchestrator


def get_session_service(request: Request) -> SessionService:
    return request.app.state.session_service


def get_speech_to_text_service(request: Request) -> SpeechToTextService:
    return request.app.state.speech_to_text_service


def get_cx_token_service(request: Request) -> CxTokenService:
    return request.app.state.cx_token_service


def get_cx_rate_limiter(request: Request) -> CxRateLimiter:
    return request.app.state.cx_rate_limiter


def get_mission_control_service(request: Request) -> MissionControlService:
    return request.app.state.mission_control_service


def get_workshop_service(request: Request) -> WorkshopService:
    return request.app.state.workshop_service


def get_architecture_service(request: Request) -> ArchitectureService:
    return request.app.state.architecture_service


def get_output_service(request: Request) -> OutputService:
    return request.app.state.output_service


def get_starter_kit_service(request: Request) -> StarterKitService:
    return request.app.state.starter_kit_service


def get_governance_service(request: Request) -> GovernanceService:
    return request.app.state.agent_orchestrator.governance_service


def get_approval_service(request: Request) -> ApprovalService:
    return request.app.state.agent_orchestrator.approval_service


def get_memory_service(request: Request) -> MemoryService:
    return request.app.state.agent_orchestrator.memory_service


def get_replay_service(request: Request) -> ReplayService:
    return request.app.state.replay_service


def get_traceability_service(request: Request) -> TraceabilityService:
    return request.app.state.traceability_service


def get_foundry_inventory_service(request: Request) -> FoundryAgentInventoryService:
    return request.app.state.foundry_inventory_service


def get_foundry_lifecycle_service(request: Request) -> FoundryAgentLifecycleService:
    return request.app.state.foundry_lifecycle_service


def get_foundry_synchronization_service(request: Request) -> FoundryAgentSynchronizationService | None:
    return request.app.state.foundry_synchronization_service
