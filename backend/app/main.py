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

from app.agents.foundry.agent_synchronization_service import FoundryAgentSynchronizationService
from app.agents.foundry.errors import FoundryAgentSynchronizationError, FoundryUnavailableError
from app.agents.foundry.project_service import FoundryProjectService
from app.api import (
    agents,
    approvals,
    architecture,
    debugging,
    deploy_launch,
    foundry_admin,
    health,
    ingestion,
    memory,
    model_catalog,
    outputs,
    peer_review,
    prototype_admin,
    replay,
    requirements,
    sessions,
    uploads,
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
from app.deploy_launch.mission_agent_provisioning_service import (
    create_mission_agent_provisioning_service,
)
from app.deploy_launch.mission_identity_service import create_mission_identity_service
from app.deploy_launch.pipeline_service import create_deployment_pipeline_service
from app.deploy_launch.prototype_authentication_service import (
    create_prototype_authentication_service,
)
from app.governance.replay_service import ReplayService
from app.governance.traceability_service import TraceabilityService
from app.orchestration.agent_orchestrator import create_agent_orchestrator
from app.prompts.registry import PromptRegistry
from app.repositories.deployment_run_repository import CosmosDeploymentRunRepository
from app.repositories.document_store import CosmosDocumentStore
from app.repositories.session_repository import CosmosSessionRepository
from app.security.token_validator import create_token_validator
from app.services.architecture_service import create_architecture_service
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
from app.services.workshop_service import create_workshop_service
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
    """Return True if ``create_agent_gateway`` will resolve to ``AzureAgentGateway``.

    Mirrors the resolution logic in ``app.agents.gateway.create_agent_gateway``
    exactly, without constructing anything: used to decide whether
    ``FoundryAgentSynchronizationService`` should run at startup - agents
    that will only ever execute through ``LocalAgentGateway`` have no need
    for their (possibly absent or stale) ``foundry_agent_id`` verified.
    """

    return bool(
        not settings.allow_local_agents
        and settings.azure_foundry_endpoint
        and settings.azure_foundry_project_name
    )


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
        try:
            runner.run_or_raise(resolved_settings)
        except StartupValidationError:
            logger.critical("Application failed fail-closed startup validation.")
            raise

        # Build the Phase 7 service graph once at startup. Every service
        # below wraps the single AgentOrchestrator instance, so every
        # request sees consistent orchestration/governance/memory state.
        app.state.token_validator = create_token_validator(resolved_settings)
        document_store: CosmosDocumentStore | None = None
        session_repository = None
        deployment_run_repository = None
        if resolved_settings.memory_store_backend == "cosmos_db":
            document_store = CosmosDocumentStore(
                endpoint=resolved_settings.memory_store_endpoint or "",
                database_name=resolved_settings.memory_store_database_name,
                container_name=resolved_settings.memory_store_container_name,
            )
            session_repository = CosmosSessionRepository(store=document_store)
            deployment_run_repository = CosmosDeploymentRunRepository(store=document_store)
        app.state.document_store = document_store
        orchestrator = create_agent_orchestrator(settings=resolved_settings)
        app.state.agent_orchestrator = orchestrator
        app.state.model_catalog_service = create_model_catalog_service(
            settings=resolved_settings,
            agent_registry=orchestrator.agent_registry,
        )

        # config/agents/*.yaml -> FoundryAgentSynchronizationService ->
        # Azure AI Foundry -> foundryAgentReference verified -> startup
        # allowed. Runs only when agents will actually execute through
        # AzureAgentGateway; fails closed (propagates, app never becomes
        # ready) if Foundry is unreachable or a configured foundry_agent_id
        # does not resolve to a real Foundry agent resource.
        if _uses_azure_agent_gateway(resolved_settings):
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
        )
        app.state.session_service = session_service
        app.state.speech_to_text_service = create_speech_to_text_service(resolved_settings)
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
        prototype_authentication_service = create_prototype_authentication_service(
            settings=resolved_settings
        )
        app.state.prototype_authentication_service = prototype_authentication_service
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
                settings=resolved_settings
            ),
            prototype_authentication_service=prototype_authentication_service,
            run_repository=deployment_run_repository,
        )
        await app.state.deployment_pipeline_service.initialize()
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
            await app.state.prototype_authentication_service.close()
            await app.state.token_validator.close()
            if app.state.document_store is not None:
                await app.state.document_store.close()

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
    app.include_router(sessions.router)
    app.include_router(uploads.router)
    app.include_router(ingestion.router)
    app.include_router(workflows.router)
    app.include_router(model_catalog.router)
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

    return app


app = create_app()
