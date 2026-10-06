"""FastAPI application entrypoint for the Genie backend.

Phase 7 adds the HTTP API layer (``app.api``) and application service
layer (``app.services``) on top of the fail-closed bootstrap Phase 1
established: every business router is included below and wired to a
service graph built once at startup, after ``StartupValidationRunner``
passes.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from azure.identity.aio import DefaultAzureCredential

from app.agents.foundry.agent_synchronization_service import FoundryAgentSynchronizationService
from app.agents.foundry.errors import FoundryAgentSynchronizationError, FoundryUnavailableError
from app.agents.foundry.project_service import FoundryProjectService
from app.api import (
    agents,
    approvals,
    architecture,
    auth,
    debugging,
    deploy_launch,
    discovery,
    foundry_admin,
    health,
    ingestion,
    iq,
    iq_connections,
    iq_diagnostics,
    memory,
    model_catalog,
    modernization,
    outputs,
    phase_tracking,
    peer_review,
    platform_config,
    production_promotion,
    prototype_admin,
    replay,
    repository_assessments,
    repository_connections,
    requirements,
    sessions,
    standards,
    uploads,
    well_architected,
    workflow_events,
    workflows,
    workshop,
)
from app.api.error_mapping import domain_error_handler
from app.config.settings import Settings, get_settings
from app.deploy_launch.access_policy_service import AccessPolicyService
from app.deploy_launch.backend_deployment_service import create_backend_deployment_service
from app.deploy_launch.container_app_frontend_deployment_service import (
    create_container_app_frontend_deployment_service,
)
from app.deploy_launch.data_layer_provisioning_service import (
    create_data_layer_provisioning_service,
)
from app.deploy_launch.mission_agent_provisioning_service import (
    create_mission_agent_provisioning_service,
)
from app.deploy_launch.mission_identity_service import create_mission_identity_service
from app.deploy_launch.pipeline_service import create_deployment_pipeline_service
from app.discovery.pricing_service import AzureRetailPricingService
from app.discovery.repository import CosmosDiscoveryCaseRepository
from app.discovery.service import create_discovery_service
from app.governance.replay_service import ReplayService
from app.governance.traceability_service import TraceabilityService
from app.iq.delegated_connection_manager import DelegatedMcpConnectionManager
from app.iq.delegated_connection_service import DelegatedConnectionService
from app.iq.delegated_token_broker import DelegatedTokenBroker
from app.iq.iq_diagnostics_service import IqDiagnosticsConfig, IqDiagnosticsService
from app.iq.mcp_client import IqMcpClient
from app.iq.microsoft_resource_registry import MicrosoftMcpResourceRegistry
from app.iq.models import IqProviderName
from app.iq.pending_oauth_flow import PendingOAuthFlowStore
from app.iq.repository import CosmosIqEvidenceRepository, InMemoryIqEvidenceRepository
from app.iq.router import IQRouter
from app.iq.service import IqEvidenceService, IqProviderRegistration
from app.iq.session_identity_index import SessionIdentityIndex
from app.iq.token_cache import InMemoryUserTokenCachePartition
from app.iq.work_iq_validation_service import WorkIqValidationService
from app.modernization.repository import (
    CosmosModernizationPlanRepository,
    InMemoryModernizationPlanRepository,
)
from app.modernization.deployment_repository import (
    CosmosModernizationDeploymentRepository,
    InMemoryModernizationDeploymentRepository,
)
from app.modernization.deployment_service import (
    ModernizationDeploymentError,
    create_modernization_deployment_service,
)
from app.modernization.capabilities import load_modernization_capabilities
from app.modernization.service import ModernizationService
from app.production_promotion.repository import (
    CosmosProductionPromotionRepository,
    InMemoryProductionPromotionRepository,
)
from app.production_promotion.service import ProductionPromotionService
from app.orchestration.agent_orchestrator import create_agent_orchestrator
from app.phase_tracking.repository import (
    CosmosPhaseTaskStateRepository,
    InMemoryPhaseTaskStateRepository,
)
from app.phase_tracking.evidence_verification import EvidenceVerificationService
from app.phase_tracking.service import PhaseTrackingService, load_phase_catalog
from app.prompts.registry import PromptRegistry
from app.repositories.deployment_run_repository import CosmosDeploymentRunRepository
from app.repositories.document_store import CosmosDocumentStore
from app.repositories.approval_repository import CosmosApprovalRepository
from app.repositories.governance_event_repository import CosmosGovernanceEventRepository
from app.repositories.recommendation_lineage_repository import (
    CosmosRecommendationLineageRepository,
)
from app.repositories.session_repository import CosmosSessionRepository
from app.repositories.shared_memory_repository import CosmosSharedMemoryRepository
from app.repositories.upload_repository import CosmosUploadRepository
from app.repositories.workflow_run_repository import CosmosWorkflowRunRepository
from app.repository_connections.github_mcp_client import (
    EnvironmentGitHubTokenProvider,
    GitHubMcpClient,
)
from app.repository_connections.repository import (
    CosmosRepositoryBindingRepository,
    InMemoryRepositoryBindingRepository,
)
from app.repository_connections.service import RepositoryConnectionService
from app.repository_assessment.repository import (
    CosmosRepositoryAssessmentRepository,
    InMemoryRepositoryAssessmentRepository,
)
from app.repository_assessment.service import RepositoryAssessmentService
from app.well_architected.microsoft_learn_client import MicrosoftLearnMcpClient
from app.well_architected.service import WellArchitectedQaService
from app.services.architecture_service import create_architecture_service
from app.services.document_understanding_service import create_document_understanding_service
from app.services.foundry_agent_inventory_service import FoundryAgentInventoryService
from app.services.foundry_agent_lifecycle_service import FoundryAgentLifecycleService
from app.services.foundry_agent_synchronization_service import (
    FoundryAgentSynchronizationService as RichFoundryAgentSynchronizationService,
)
from app.services.model_catalog_service import create_model_catalog_service
from app.services.output_service import create_output_service
from app.services.peer_review_service import create_peer_review_service
from app.services.requirements_service import create_requirements_service
from app.services.session_service import create_session_service
from app.security.auth_service import AuthService
from app.services.workshop_service import create_workshop_service
from app.platform_config.repository import (
    CosmosPlatformReferenceRepositoryStore,
    InMemoryPlatformReferenceRepositoryStore,
)
from app.platform_config.service import PlatformConfigService
from app.standards.architecture_reference_repository import (
    CosmosArchitectureReferenceRepository,
    InMemoryArchitectureReferenceRepository,
)
from app.standards.repository import CosmosStandardsRepository, InMemoryStandardsRepository
from app.standards.service import StandardsService
from app.transcription.speech_service import create_speech_to_text_service
from app.validation.base import StartupValidationError
from app.validation.foundry_agent_drift_validator import FoundryAgentDriftValidator
from app.validation.foundry_agent_registry_validator import FoundryAgentRegistryValidator
from app.validation.runner import StartupValidationRunner

logger = logging.getLogger(__name__)


async def _reconcile_expired_prototypes(service, *, interval_seconds: int) -> None:
    while True:
        try:
            deleted = await service.cleanup_expired()
            if deleted:
                logger.info("Deleted %d expired prototype(s).", len(deleted))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Prototype expiry reconciliation failed.")
        await asyncio.sleep(interval_seconds)


def configure_logging(settings: Settings) -> None:
    """Configure structured logging using the configured log level.

    Full structured JSON logging with correlation IDs and OpenTelemetry
    hooks is implemented alongside the API modules in later phases; Phase 1
    only establishes the log level from externalized configuration.
    """

    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _uses_azure_agent_gateway(settings: Settings) -> bool:
    """Return whether the mandatory Foundry configuration is present."""

    return bool(settings.azure_foundry_endpoint and settings.azure_foundry_project_name)


def create_app(
    settings: Settings | None = None,
    validation_runner: StartupValidationRunner | None = None,
) -> FastAPI:
    """Application factory.

    A dedicated factory (rather than relying solely on a module-level
    singleton) keeps the app fully testable: tests can inject settings and
    a validator runner to exercise both the happy path and fail-closed
    startup behavior.
    """

    resolved_settings = settings or get_settings()
    runner = validation_runner or StartupValidationRunner()
    configure_logging(resolved_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.ready = False
        app.state.settings = resolved_settings
        evidence_credential = DefaultAzureCredential()
        try:
            runner.run_or_raise(resolved_settings)
        except StartupValidationError:
            logger.critical("Application failed fail-closed startup validation.")
            raise

        # Build the Phase 7 service graph once at startup. Every service
        # below wraps the single AgentOrchestrator instance, so every
        # request sees consistent orchestration/governance/memory state.
        document_store: CosmosDocumentStore | None = None
        session_repository = None
        deployment_run_repository = None
        discovery_case_repository = None
        upload_repository = None
        workflow_run_repository = None
        shared_memory_repository = None
        repository_binding_repository = None
        repository_assessment_repository = None
        standards_repository = None
        architecture_reference_repository = None
        platform_reference_repository_store = None
        iq_evidence_repository = None
        modernization_plan_repository = None
        modernization_deployment_repository = None
        production_promotion_repository = None
        phase_task_state_repository = None
        governance_event_repository = None
        approval_repository = None
        recommendation_lineage_repository = None
        if resolved_settings.memory_store_backend == "cosmos_db":
            document_store = CosmosDocumentStore(
                endpoint=resolved_settings.memory_store_endpoint or "",
                database_name=resolved_settings.memory_store_database_name,
                container_name=resolved_settings.memory_store_container_name,
            )
            session_repository = CosmosSessionRepository(store=document_store)
            deployment_run_repository = CosmosDeploymentRunRepository(store=document_store)
            discovery_case_repository = CosmosDiscoveryCaseRepository(store=document_store)
            upload_repository = CosmosUploadRepository(store=document_store)
            workflow_run_repository = CosmosWorkflowRunRepository(store=document_store)
            shared_memory_repository = CosmosSharedMemoryRepository(store=document_store)
            repository_binding_repository = CosmosRepositoryBindingRepository(store=document_store)
            repository_assessment_repository = CosmosRepositoryAssessmentRepository(
                store=document_store
            )
            standards_repository = CosmosStandardsRepository(store=document_store)
            architecture_reference_repository = CosmosArchitectureReferenceRepository(
                store=document_store
            )
            platform_reference_repository_store = CosmosPlatformReferenceRepositoryStore(
                store=document_store
            )
            iq_evidence_repository = CosmosIqEvidenceRepository(store=document_store)
            modernization_plan_repository = CosmosModernizationPlanRepository(store=document_store)
            modernization_deployment_repository = CosmosModernizationDeploymentRepository(
                store=document_store
            )
            production_promotion_repository = CosmosProductionPromotionRepository(
                store=document_store
            )
            phase_task_state_repository = CosmosPhaseTaskStateRepository(store=document_store)
            governance_event_repository = CosmosGovernanceEventRepository(store=document_store)
            approval_repository = CosmosApprovalRepository(store=document_store)
            recommendation_lineage_repository = CosmosRecommendationLineageRepository(
                store=document_store
            )
        app.state.document_store = document_store
        effective_standards_repository = standards_repository or InMemoryStandardsRepository()
        effective_architecture_reference_repository = (
            architecture_reference_repository or InMemoryArchitectureReferenceRepository()
        )
        effective_platform_reference_repository_store = (
            platform_reference_repository_store or InMemoryPlatformReferenceRepositoryStore()
        )
        effective_modernization_plan_repository = (
            modernization_plan_repository or InMemoryModernizationPlanRepository()
        )
        effective_modernization_deployment_repository = (
            modernization_deployment_repository or InMemoryModernizationDeploymentRepository()
        )
        orchestrator = create_agent_orchestrator(
            settings=resolved_settings,
            workflow_run_repository=workflow_run_repository,
            shared_memory_repository=shared_memory_repository,
            governance_event_repository=governance_event_repository,
            approval_repository=approval_repository,
            recommendation_lineage_repository=recommendation_lineage_repository,
            standards_repository=effective_standards_repository,
            architecture_reference_repository=effective_architecture_reference_repository,
            platform_reference_repository_store=effective_platform_reference_repository_store,
        )
        app.state.agent_orchestrator = orchestrator
        app.state.model_catalog_service = create_model_catalog_service(
            settings=resolved_settings,
            agent_registry=orchestrator.agent_registry,
        )

        # config/agents/*.yaml -> FoundryAgentSynchronizationService ->
        # Azure AI Foundry -> foundryAgentReference verified -> startup
        # allowed. Validation guarantees this configuration exists. Any
        # authentication, connectivity, missing-agent, or drift failure aborts
        # startup before the application accepts traffic.
        try:
            project_service = FoundryProjectService(
                endpoint=resolved_settings.azure_foundry_endpoint or "",
                project_name=resolved_settings.azure_foundry_project_name or "",
            )
            synchronization_service = FoundryAgentSynchronizationService(project_service)
            await asyncio.to_thread(
                synchronization_service.synchronize, orchestrator.agent_registry
            )
        except (FoundryAgentSynchronizationError, FoundryUnavailableError):
            logger.critical("Foundry agent synchronization failed; startup aborted.")
            raise
        logger.info("Foundry agent synchronization verified all configured agent references.")

        # Phase 10A: Foundry agent provisioning, inventory, lifecycle, and
        # drift management - layered above (never redesigning) the
        # AgentRegistry -> AzureAgentGateway -> Azure AI Foundry chain
        # above. Config-only fail-closed checks (metadata completeness,
        # structural prompt/lifecycle drift) run for every deployment;
        # network-dependent inventory synchronization/drift detection only
        # runs when agents will actually execute through AzureAgentGateway.
        registry_validator_result = FoundryAgentRegistryValidator().validate(resolved_settings)
        drift_validator_result = FoundryAgentDriftValidator().validate(resolved_settings)
        if not registry_validator_result.passed or not drift_validator_result.passed:
            for result in (registry_validator_result, drift_validator_result):
                for issue in result.issues:
                    logger.error("[%s] %s", issue.validator, issue.message)
            logger.critical("Foundry agent registry/drift validation failed; startup aborted.")
            raise StartupValidationError([registry_validator_result, drift_validator_result])

        app.state.foundry_inventory_service = FoundryAgentInventoryService()
        app.state.foundry_lifecycle_service = FoundryAgentLifecycleService(
            governance_service=orchestrator.governance_service,
            inventory_service=app.state.foundry_inventory_service,
        )
        for agent in orchestrator.agent_registry.list():
            if agent.enabled:
                await app.state.foundry_inventory_service.seed_from_agent(agent)

        app.state.foundry_synchronization_service = None
        if _uses_azure_agent_gateway(resolved_settings):
            rich_project_service = FoundryProjectService(
                endpoint=resolved_settings.azure_foundry_endpoint or "",
                project_name=resolved_settings.azure_foundry_project_name or "",
            )
            prompt_registry = PromptRegistry.load(resolved_settings.prompts_path)
            rich_synchronization_service = RichFoundryAgentSynchronizationService(
                project_service=rich_project_service,
                prompt_registry=prompt_registry,
                inventory_service=app.state.foundry_inventory_service,
            )
            await rich_synchronization_service.synchronize(orchestrator.agent_registry)
            app.state.foundry_synchronization_service = rich_synchronization_service

        session_service = create_session_service(
            orchestrator=orchestrator,
            session_repository=session_repository,
            upload_repository=upload_repository,
        )
        app.state.session_service = session_service
        app.state.auth_service = AuthService(
            enabled=resolved_settings.auth_enabled,
            signing_key_env_var=resolved_settings.auth_token_signing_key_env_var,
            users_env_var=resolved_settings.auth_users_env_var,
            token_ttl_seconds=resolved_settings.auth_token_ttl_seconds,
        )
        github_mcp_client = None
        if resolved_settings.github_mcp_enabled:
            github_mcp_client = GitHubMcpClient(
                endpoint=resolved_settings.github_mcp_endpoint or "",
                token_provider=EnvironmentGitHubTokenProvider(
                    environment_variable=resolved_settings.github_mcp_token_env_var or "",
                ),
                timeout_seconds=resolved_settings.github_mcp_timeout_seconds,
            )
        effective_binding_repository = (
            repository_binding_repository or InMemoryRepositoryBindingRepository()
        )
        app.state.repository_connection_service = RepositoryConnectionService(
            client=github_mcp_client,
            repository=effective_binding_repository,
            session_service=session_service,
            governance_service=orchestrator.governance_service,
        )
        effective_assessment_repository = (
            repository_assessment_repository or InMemoryRepositoryAssessmentRepository()
        )
        app.state.repository_assessment_service = RepositoryAssessmentService(
            client=github_mcp_client,
            binding_repository=effective_binding_repository,
            assessment_repository=effective_assessment_repository,
            session_service=session_service,
            governance_service=orchestrator.governance_service,
            max_files=resolved_settings.repository_assessment_max_files,
            max_depth=resolved_settings.repository_assessment_max_depth,
            max_source_bytes=resolved_settings.repository_assessment_max_source_bytes,
            orchestrator=orchestrator,
        )
        app.state.standards_service = StandardsService(
            client=github_mcp_client,
            binding_repository=effective_binding_repository,
            assessment_repository=effective_assessment_repository,
            standards_repository=effective_standards_repository,
            architecture_reference_repository=effective_architecture_reference_repository,
            session_service=session_service,
            governance_service=orchestrator.governance_service,
            max_files=resolved_settings.repository_assessment_max_files,
            max_depth=resolved_settings.repository_assessment_max_depth,
        )
        app.state.well_architected_qa_service = WellArchitectedQaService(
            learn_client=MicrosoftLearnMcpClient(
                endpoint=resolved_settings.microsoft_learn_mcp_endpoint,
                timeout_seconds=resolved_settings.microsoft_learn_mcp_timeout_seconds,
            ),
            orchestrator=orchestrator if resolved_settings.well_architected_qa_enabled else None,
            session_service=session_service,
            governance_service=orchestrator.governance_service,
            max_search_results=resolved_settings.well_architected_max_search_results,
            max_fetched_documents=resolved_settings.well_architected_max_fetched_documents,
        )
        app.state.platform_config_service = PlatformConfigService(
            client=github_mcp_client,
            repository_store=effective_platform_reference_repository_store,
            governance_service=orchestrator.governance_service,
            max_files=resolved_settings.repository_assessment_max_files,
            max_depth=resolved_settings.repository_assessment_max_depth,
        )
        static_iq_provider_settings = (
            (
                "foundry_iq",
                resolved_settings.foundry_iq_enabled,
                resolved_settings.foundry_iq_mcp_endpoint,
                resolved_settings.foundry_iq_token_env_var,
                resolved_settings.foundry_iq_retrieve_tool,
                resolved_settings.foundry_iq_query_argument,
            ),
            (
                "foundry_mcp",
                resolved_settings.foundry_mcp_enabled,
                resolved_settings.foundry_mcp_endpoint,
                resolved_settings.foundry_mcp_token_env_var,
                resolved_settings.foundry_mcp_retrieve_tool,
                resolved_settings.foundry_mcp_query_argument,
            ),
        )
        iq_registrations = [
            IqProviderRegistration(
                name=name,
                enabled=enabled,
                client=(
                    IqMcpClient(
                        endpoint=endpoint or "",
                        token_environment_variable=token_env_var or "",
                        timeout_seconds=resolved_settings.iq_mcp_timeout_seconds,
                    )
                    if enabled
                    else None
                ),
                retrieve_tool=retrieve_tool,
                query_argument=query_argument,
            )
            for name, enabled, endpoint, token_env_var, retrieve_tool, query_argument in static_iq_provider_settings
        ]
        # Delegated Microsoft Entra OAuth providers (Work IQ / Fabric IQ) -
        # no static client here; IqEvidenceService resolves a per-session
        # connection through DelegatedConnectionService instead. See
        # docs/architecture/genie-sas-microsoft-iq.md.
        delegated_enabled_providers: frozenset[IqProviderName] = frozenset(
            name
            for name, enabled in (
                ("work_iq", resolved_settings.work_iq_enabled),
                ("fabric_iq", resolved_settings.fabric_iq_enabled),
            )
            if enabled
        )
        for name, enabled, retrieve_tool, query_argument in (
            ("work_iq", resolved_settings.work_iq_enabled, resolved_settings.work_iq_retrieve_tool, resolved_settings.work_iq_query_argument),
            ("fabric_iq", resolved_settings.fabric_iq_enabled, resolved_settings.fabric_iq_retrieve_tool, resolved_settings.fabric_iq_query_argument),
        ):
            iq_registrations.append(
                IqProviderRegistration(
                    name=name,
                    enabled=enabled,
                    client=None,
                    retrieve_tool=retrieve_tool,
                    query_argument=query_argument,
                )
            )
        delegated_connection_service: DelegatedConnectionService | None = None
        if delegated_enabled_providers:
            resource_registry = MicrosoftMcpResourceRegistry.from_settings(
                work_iq_mcp_endpoint=resolved_settings.work_iq_mcp_endpoint,
                work_iq_scopes=resolved_settings.work_iq_scopes_list,
                fabric_iq_mcp_endpoint=resolved_settings.fabric_iq_mcp_endpoint,
                fabric_iq_scopes=resolved_settings.fabric_iq_scopes_list,
            )
            token_broker = DelegatedTokenBroker(
                registry=resource_registry,
                client_id=resolved_settings.iq_oauth_client_id or "",
                client_secret_env_var=resolved_settings.iq_oauth_client_secret_env_var or "",
                redirect_uri=resolved_settings.iq_oauth_redirect_uri or "",
                allowed_tenant_id=resolved_settings.iq_oauth_tenant_id or "",
                timeout_seconds=resolved_settings.iq_mcp_timeout_seconds,
            )
            connection_manager = DelegatedMcpConnectionManager(
                registry=resource_registry,
                token_broker=token_broker,
                token_cache=InMemoryUserTokenCachePartition(),
                mcp_timeout_seconds=resolved_settings.iq_mcp_timeout_seconds,
            )
            delegated_connection_service = DelegatedConnectionService(
                connection_manager=connection_manager,
                token_broker=token_broker,
                pending_flow_store=PendingOAuthFlowStore(
                    ttl_seconds=resolved_settings.iq_oauth_state_ttl_seconds
                ),
                session_identity_index=SessionIdentityIndex(),
                session_service=session_service,
                governance_service=orchestrator.governance_service,
                enabled_providers=delegated_enabled_providers,
            )
        app.state.delegated_connection_service = delegated_connection_service
        app.state.iq_evidence_service = IqEvidenceService(
            providers=iq_registrations,
            repository=iq_evidence_repository or InMemoryIqEvidenceRepository(),
            session_service=session_service,
            governance_service=orchestrator.governance_service,
            memory_service=orchestrator.memory_service,
            promoter_agent=orchestrator.agent_registry.get("genie-orchestrator"),
            router=IQRouter(),
            delegated_connection_service=delegated_connection_service,
        )
        # IQ providers are enrichment-only (see
        # docs/architecture/genie-sas-microsoft-iq.md "Optional IQ
        # enrichment"): an unreachable, unauthenticated, or misconfigured
        # provider must not prevent Genie itself from starting. Only log
        # each enabled-but-not-available provider so operators can see it
        # in the health/readiness surface without a startup crash.
        for provider_status in await app.state.iq_evidence_service.provider_statuses():
            if provider_status.enabled and not provider_status.connected:
                logger.warning(
                    "IQ provider '%s' is not available (%s): %s",
                    provider_status.provider,
                    provider_status.status,
                    provider_status.detail,
                )
        # Development-only diagnostics/validation surfaces (see
        # app/api/iq_diagnostics.py - hard-gated to non-production by
        # route, constructed unconditionally here so the gate is the
        # single source of truth rather than duplicated at construction
        # time too).
        app.state.iq_diagnostics_service = IqDiagnosticsService(
            config=IqDiagnosticsConfig(
                work_iq_enabled=resolved_settings.work_iq_enabled,
                tenant_configured=bool(resolved_settings.iq_oauth_tenant_id),
                client_configured=bool(resolved_settings.iq_oauth_client_id),
                redirect_uri_configured=bool(resolved_settings.iq_oauth_redirect_uri),
                work_iq_endpoint_configured=bool(resolved_settings.work_iq_mcp_endpoint),
            ),
            connection_service=delegated_connection_service,
            session_service=session_service,
        )
        app.state.work_iq_validation_service = (
            WorkIqValidationService(
                connection_service=delegated_connection_service,
                session_service=session_service,
                governance_service=orchestrator.governance_service,
                retrieve_tool=resolved_settings.work_iq_retrieve_tool or "",
                query_argument=resolved_settings.work_iq_query_argument,
            )
            if delegated_connection_service is not None and resolved_settings.work_iq_enabled
            else None
        )
        app.state.modernization_service = ModernizationService(
            client=github_mcp_client,
            plan_repository=effective_modernization_plan_repository,
            binding_repository=effective_binding_repository,
            assessment_repository=effective_assessment_repository,
            standards_repository=effective_standards_repository,
            architecture_reference_repository=effective_architecture_reference_repository,
            session_service=session_service,
            orchestrator=orchestrator,
            approval_service=orchestrator.approval_service,
            governance_service=orchestrator.governance_service,
            capability_catalog=load_modernization_capabilities(
                resolved_settings.workflows_path
            ),
            platform_reference_repository_store=effective_platform_reference_repository_store,
            pricing_service=AzureRetailPricingService(
                endpoint=resolved_settings.azure_retail_prices_endpoint
            ),
        )
        # Real deployment is an additive capability on top of modernization
        # plans, not a core required one - unlike create_backend_deployment_
        # service (always called, always fails closed), missing settings
        # here simply leave this feature unavailable (503 via
        # get_modernization_deployment_service) rather than aborting startup.
        try:
            app.state.modernization_deployment_service = create_modernization_deployment_service(
                settings=resolved_settings,
                plan_repository=effective_modernization_plan_repository,
                deployment_repository=effective_modernization_deployment_repository,
                orchestrator=orchestrator,
                approval_service=orchestrator.approval_service,
                governance_service=orchestrator.governance_service,
            )
        except ModernizationDeploymentError:
            app.state.modernization_deployment_service = None
        app.state.phase_tracking_service = PhaseTrackingService(
            catalog=load_phase_catalog(resolved_settings.workflows_path),
            repository=phase_task_state_repository or InMemoryPhaseTaskStateRepository(),
            session_service=session_service,
            evidence_verification_service=EvidenceVerificationService(
                github_client=github_mcp_client,
                repository_binding_repository=effective_binding_repository,
                azure_credential=evidence_credential,
                azure_subscription_id=resolved_settings.azure_subscription_id,
            ),
        )
        if resolved_settings.github_mcp_enabled:
            await app.state.repository_connection_service.connect()
        app.state.discovery_service = create_discovery_service(
            session_service=session_service,
            repository=discovery_case_repository,
            orchestrator=orchestrator,
            governance_service=orchestrator.governance_service,
            memory_service=orchestrator.memory_service,
            model_catalog_service=app.state.model_catalog_service,
            pricing_service=AzureRetailPricingService(
                endpoint=resolved_settings.azure_retail_prices_endpoint
            ),
        )
        app.state.speech_to_text_service = create_speech_to_text_service(resolved_settings)
        app.state.document_understanding_service = create_document_understanding_service(
            resolved_settings
        )
        await app.state.document_understanding_service.validate_ready()
        app.state.workshop_service = create_workshop_service(
            orchestrator=orchestrator, session_service=session_service
        )
        app.state.architecture_service = create_architecture_service(
            orchestrator=orchestrator, session_service=session_service
        )
        app.state.output_service = create_output_service(
            orchestrator=orchestrator, session_service=session_service
        )
        app.state.requirements_service = create_requirements_service(
            orchestrator=orchestrator,
            session_service=session_service,
            qualification_step_id=resolved_settings.requirements_qualification_step_id,
        )
        app.state.peer_review_service = create_peer_review_service(
            orchestrator=orchestrator,
            session_service=session_service,
            gated_step_ids=resolved_settings.gated_fix_step_ids,
        )
        app.state.replay_service = ReplayService(
            governance_service=orchestrator.governance_service,
            approval_service=orchestrator.approval_service,
            recommendation_lineage_service=orchestrator.recommendation_lineage_service,
            decision_graph_service=orchestrator.decision_graph_service,
        )
        app.state.traceability_service = TraceabilityService(
            recommendation_lineage_service=orchestrator.recommendation_lineage_service,
            approval_service=orchestrator.approval_service,
            governance_service=orchestrator.governance_service,
        )
        app.state.deployment_pipeline_service = create_deployment_pipeline_service(
            settings=resolved_settings,
            orchestrator=orchestrator,
            session_service=session_service,
            event_bus=orchestrator.workflow_event_bus,
            access_policy_service=AccessPolicyService(
                agent_registry=orchestrator.agent_registry,
                mission_identity_service=create_mission_identity_service(
                    settings=resolved_settings,
                    subscription_id=resolved_settings.azure_subscription_id or "unknown",
                    resource_group_name=resolved_settings.deployment_resource_group or "unknown",
                ),
                acr_id=(
                    f"/subscriptions/{resolved_settings.azure_subscription_id}/resourceGroups/"
                    f"{resolved_settings.deployment_resource_group}/providers/"
                    f"Microsoft.ContainerRegistry/registries/"
                    f"{resolved_settings.deployment_acr_name}"
                    if resolved_settings.prototype_api_gateway_enabled
                    else None
                ),
            ),
            mission_identity_service=create_mission_identity_service(
                settings=resolved_settings,
                subscription_id=resolved_settings.azure_subscription_id or "unknown",
                resource_group_name=resolved_settings.deployment_resource_group or "unknown",
            ),
            mission_agent_provisioning_service=create_mission_agent_provisioning_service(
                settings=resolved_settings
            ),
            backend_deployment_service=create_backend_deployment_service(settings=resolved_settings),
            frontend_deployment_service=create_container_app_frontend_deployment_service(
                settings=                resolved_settings
            ),
            data_layer_provisioning_service=create_data_layer_provisioning_service(
                subscription_id=resolved_settings.azure_subscription_id,
                location=resolved_settings.deployment_location,
            ),
            run_repository=deployment_run_repository,
        )
        await app.state.deployment_pipeline_service.initialize()
        if (
            resolved_settings.azure_subscription_id
            and resolved_settings.production_resource_group
        ):
            app.state.production_promotion_service = ProductionPromotionService(
                subscription_id=resolved_settings.azure_subscription_id,
                allowed_resource_group=resolved_settings.production_resource_group,
                repository=production_promotion_repository
                or InMemoryProductionPromotionRepository(),
                pipeline_service=app.state.deployment_pipeline_service,
                session_service=session_service,
                approval_service=orchestrator.approval_service,
                canary_weight_percent=resolved_settings.production_canary_weight_percent,
                health_timeout_seconds=resolved_settings.production_health_timeout_seconds,
            )
        app.state.prototype_cleanup_task = asyncio.create_task(
            _reconcile_expired_prototypes(
                app.state.deployment_pipeline_service,
                interval_seconds=resolved_settings.prototype_cleanup_interval_seconds,
            ),
            name="prototype-expiry-reconciler",
        )

        app.state.ready = True
        logger.info(
            "Genie backend started (%s environment).",
            resolved_settings.environment,
        )
        try:
            yield
        finally:
            app.state.ready = False
            app.state.prototype_cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await app.state.prototype_cleanup_task
            if app.state.document_store is not None:
                await app.state.document_store.close()
            await evidence_credential.close()

    app = FastAPI(
        title="Genie Agentic Experience Center",
        version="1.0.0",
        lifespan=lifespan,
    )

    if resolved_settings.cors_allowed_origins_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=resolved_settings.cors_allowed_origins_list,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.add_exception_handler(RuntimeError, domain_error_handler)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(sessions.router)
    app.include_router(uploads.router)
    app.include_router(ingestion.router)
    app.include_router(iq.router)
    app.include_router(iq_connections.router)
    app.include_router(iq_diagnostics.router)
    app.include_router(workflows.router)
    app.include_router(model_catalog.router)
    app.include_router(modernization.router)
    app.include_router(production_promotion.router)
    app.include_router(phase_tracking.router)
    app.include_router(workflow_events.router)
    app.include_router(agents.router)
    app.include_router(memory.router)
    app.include_router(peer_review.router)
    app.include_router(prototype_admin.router)
    app.include_router(approvals.router)
    app.include_router(architecture.router)
    app.include_router(workshop.router)
    app.include_router(outputs.router)
    app.include_router(requirements.router)
    app.include_router(debugging.router)
    app.include_router(replay.router)
    app.include_router(foundry_admin.router)
    app.include_router(deploy_launch.router)
    app.include_router(discovery.router)
    app.include_router(repository_connections.router)
    app.include_router(repository_assessments.router)
    app.include_router(well_architected.router)
    app.include_router(standards.router)
    app.include_router(standards.architecture_reference_router)
    app.include_router(platform_config.router)

    return app


app = create_app()
