"""Shared FastAPI dependency wiring for Phase 7 API routers.

Each dependency resolves a service constructed once at application startup
(see ``app.main.create_app``) and stored on ``app.state`` - this module
performs no business logic itself, keeping every API route thin per "API
routes must remain thin" in ``.github/copilot-instructions.md``.
"""
from __future__ import annotations

from fastapi import HTTPException, Request, status

from app.deploy_launch.pipeline_service import DeploymentPipelineService
from app.discovery.service import DiscoveryService
from app.governance.approval_service import ApprovalService
from app.governance.governance_service import GovernanceService
from app.governance.replay_service import ReplayService
from app.governance.traceability_service import TraceabilityService
from app.iq.delegated_connection_service import DelegatedConnectionService
from app.iq.iq_diagnostics_service import IqDiagnosticsService
from app.iq.work_iq_validation_service import WorkIqValidationService
from app.memory.memory_service import MemoryService
from app.modernization.deployment_service import ModernizationDeploymentService
from app.modernization.service import ModernizationService
from app.production_promotion.service import ProductionPromotionService
from app.iq.service import IqEvidenceService
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.orchestration.workflow_event_bus import WorkflowEventBus
from app.phase_tracking.service import PhaseTrackingService
from app.repository_connections.service import RepositoryConnectionService
from app.repository_assessment.service import RepositoryAssessmentService
from app.well_architected.service import WellArchitectedQaService
from app.services.architecture_service import ArchitectureService
from app.services.document_understanding_service import DocumentUnderstandingService
from app.services.foundry_agent_inventory_service import FoundryAgentInventoryService
from app.services.foundry_agent_lifecycle_service import FoundryAgentLifecycleService
from app.services.foundry_agent_synchronization_service import FoundryAgentSynchronizationService
from app.services.model_catalog_service import ModelCatalogService
from app.services.output_service import OutputService
from app.services.peer_review_service import PeerReviewService
from app.services.requirements_service import RequirementsService
from app.services.session_service import SessionService
from app.services.workshop_service import WorkshopService
from app.platform_config.service import PlatformConfigService
from app.standards.service import StandardsService
from app.transcription.speech_service import SpeechToTextService
from app.security.auth_service import AuthService

__all__ = [
    "get_agent_orchestrator",
    "get_approval_service",
    "get_architecture_service",
    "get_auth_service",
    "get_delegated_connection_service",
    "get_deployment_pipeline_service",
    "get_discovery_service",
    "get_document_understanding_service",
    "get_foundry_inventory_service",
    "get_foundry_lifecycle_service",
    "get_foundry_synchronization_service",
    "get_governance_service",
    "get_iq_diagnostics_service",
    "get_memory_service",
    "get_iq_evidence_service",
    "get_model_catalog_service",
    "get_modernization_service",
    "get_output_service",
    "get_peer_review_service",
    "get_phase_tracking_service",
    "get_production_promotion_service",
    "get_replay_service",
    "get_repository_connection_service",
    "get_repository_assessment_service",
    "get_requirements_service",
    "get_session_service",
    "get_speech_to_text_service",
    "get_standards_service",
    "get_platform_config_service",
    "get_traceability_service",
    "get_well_architected_qa_service",
    "get_workflow_event_bus",
    "get_work_iq_validation_service",
    "get_workshop_service",
]


def get_agent_orchestrator(request: Request) -> AgentOrchestrator:
    return request.app.state.agent_orchestrator


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


def get_session_service(request: Request) -> SessionService:
    return request.app.state.session_service


def get_discovery_service(request: Request) -> DiscoveryService:
    return request.app.state.discovery_service


def get_document_understanding_service(request: Request) -> DocumentUnderstandingService:
    return request.app.state.document_understanding_service


def get_speech_to_text_service(request: Request) -> SpeechToTextService:
    return request.app.state.speech_to_text_service


def get_workshop_service(request: Request) -> WorkshopService:
    return request.app.state.workshop_service


def get_architecture_service(request: Request) -> ArchitectureService:
    return request.app.state.architecture_service


def get_output_service(request: Request) -> OutputService:
    return request.app.state.output_service


def get_requirements_service(request: Request) -> RequirementsService:
    return request.app.state.requirements_service


def get_peer_review_service(request: Request) -> PeerReviewService:
    return request.app.state.peer_review_service


def get_governance_service(request: Request) -> GovernanceService:
    return request.app.state.agent_orchestrator.governance_service


def get_approval_service(request: Request) -> ApprovalService:
    return request.app.state.agent_orchestrator.approval_service


def get_workflow_event_bus(request: Request) -> WorkflowEventBus:
    return request.app.state.agent_orchestrator.workflow_event_bus


def get_memory_service(request: Request) -> MemoryService:
    return request.app.state.agent_orchestrator.memory_service


def get_iq_evidence_service(request: Request) -> IqEvidenceService:
    return request.app.state.iq_evidence_service


def get_modernization_service(request: Request) -> ModernizationService:
    return request.app.state.modernization_service


def get_modernization_deployment_service(request: Request) -> ModernizationDeploymentService:
    service = getattr(request.app.state, "modernization_deployment_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Real modernization deployment is not configured for this environment.",
        )
    return service


def get_production_promotion_service(request: Request) -> ProductionPromotionService:
    service = getattr(request.app.state, "production_promotion_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Production promotion is not configured.",
        )
    return service


def get_delegated_connection_service(request: Request) -> DelegatedConnectionService:
    service = getattr(request.app.state, "delegated_connection_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No delegated Microsoft IQ provider (Work IQ / Fabric IQ) is enabled.",
        )
    return service


def get_iq_diagnostics_service(request: Request) -> IqDiagnosticsService:
    service = getattr(request.app.state, "iq_diagnostics_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="IQ diagnostics are not configured.",
        )
    return service


def get_work_iq_validation_service(request: Request) -> WorkIqValidationService:
    service = getattr(request.app.state, "work_iq_validation_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Work IQ is not enabled, or no delegated connection service is configured.",
        )
    return service


def get_phase_tracking_service(request: Request) -> PhaseTrackingService:
    return request.app.state.phase_tracking_service


def get_model_catalog_service(request: Request) -> ModelCatalogService:
    return request.app.state.model_catalog_service


def get_replay_service(request: Request) -> ReplayService:
    return request.app.state.replay_service


def get_repository_connection_service(request: Request) -> RepositoryConnectionService:
    return request.app.state.repository_connection_service


def get_repository_assessment_service(request: Request) -> RepositoryAssessmentService:
    return request.app.state.repository_assessment_service


def get_well_architected_qa_service(request: Request) -> WellArchitectedQaService:
    return request.app.state.well_architected_qa_service


def get_standards_service(request: Request) -> StandardsService:
    return request.app.state.standards_service


def get_platform_config_service(request: Request) -> PlatformConfigService:
    return request.app.state.platform_config_service


def get_deployment_pipeline_service(request: Request) -> DeploymentPipelineService:
    return request.app.state.deployment_pipeline_service


def get_traceability_service(request: Request) -> TraceabilityService:
    return request.app.state.traceability_service


def get_foundry_inventory_service(request: Request) -> FoundryAgentInventoryService:
    return request.app.state.foundry_inventory_service


def get_foundry_lifecycle_service(request: Request) -> FoundryAgentLifecycleService:
    return request.app.state.foundry_lifecycle_service


def get_foundry_synchronization_service(request: Request) -> FoundryAgentSynchronizationService | None:
    return request.app.state.foundry_synchronization_service
