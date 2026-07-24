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
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.agents.foundry.agent_synchronization_service import FoundryAgentSynchronizationService
from app.agents.foundry.errors import FoundryAgentSynchronizationError, FoundryUnavailableError
from app.agents.foundry.project_service import FoundryProjectService
from app.api import (
    agents,
    approvals,
    architecture,
    debugging,
    foundry_admin,
    governance,
    health,
    ingestion,
    memory,
    mission_control,
    outputs,
    replay,
    sessions,
    uploads,
    workflows,
    workshop,
)
from app.api.error_mapping import domain_error_handler
from app.config.settings import Settings, get_settings
from app.governance.replay_service import ReplayService
from app.governance.traceability_service import TraceabilityService
from app.orchestration.agent_orchestrator import create_agent_orchestrator
from app.prompts.registry import PromptRegistry
from app.security.token_validator import create_token_validator
from app.services.architecture_service import create_architecture_service
from app.services.foundry_agent_inventory_service import FoundryAgentInventoryService
from app.services.foundry_agent_lifecycle_service import FoundryAgentLifecycleService
from app.services.foundry_agent_synchronization_service import (
    FoundryAgentSynchronizationService as RichFoundryAgentSynchronizationService,
)
from app.services.mission_control_service import create_mission_control_service
from app.services.output_service import create_output_service
from app.services.session_service import create_session_service
from app.services.workshop_service import create_workshop_service
from app.validation.base import StartupValidationError
from app.validation.foundry_agent_drift_validator import FoundryAgentDriftValidator
from app.validation.foundry_agent_registry_validator import FoundryAgentRegistryValidator
from app.validation.runner import StartupValidationRunner

logger = logging.getLogger(__name__)


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
    exactly, without constructing anything: production always uses the Azure
    gateway; local/dev mode only uses it when local execution is disallowed
    and Foundry is configured. Used to decide whether
    ``FoundryAgentSynchronizationService`` should run at startup - agents
    that will only ever execute through ``LocalAgentGateway`` have no need
    for their (possibly absent or stale) ``foundry_agent_id`` verified.
    """

    if settings.provider_mode == "production":
        return True
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
        orchestrator = create_agent_orchestrator(settings=resolved_settings)
        app.state.agent_orchestrator = orchestrator

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
            report = await rich_synchronization_service.synchronize(orchestrator.agent_registry)
            critical_drift = any(
                drift_report.has_critical_issues
                for drift_report in rich_synchronization_service.drift_reports().values()
            )
            if resolved_settings.provider_mode == "production" and (
                report.has_blocking_failures or critical_drift
            ):
                logger.critical(
                    "Foundry agent inventory synchronization reported blocking failures "
                    "or critical drift; startup aborted."
                )
                raise FoundryAgentSynchronizationError(
                    "Foundry agent inventory synchronization failed or detected critical "
                    "drift in production."
                )
            app.state.foundry_synchronization_service = rich_synchronization_service

        session_service = create_session_service(orchestrator=orchestrator)
        app.state.session_service = session_service
        app.state.mission_control_service = create_mission_control_service(
            orchestrator=orchestrator, session_service=session_service
        )
        app.state.workshop_service = create_workshop_service(
            orchestrator=orchestrator, session_service=session_service
        )
        app.state.architecture_service = create_architecture_service(
            orchestrator=orchestrator, session_service=session_service
        )
        app.state.output_service = create_output_service(
            orchestrator=orchestrator, session_service=session_service
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

        app.state.ready = True
        logger.info(
            "Genie backend started in '%s' provider mode (%s environment).",
            resolved_settings.provider_mode,
            resolved_settings.environment,
        )
        yield
        app.state.ready = False

    app = FastAPI(
        title="Genie Agentic Experience Center",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_exception_handler(RuntimeError, domain_error_handler)

    app.include_router(health.router)
    app.include_router(sessions.router)
    app.include_router(uploads.router)
    app.include_router(ingestion.router)
    app.include_router(workflows.router)
    app.include_router(mission_control.router)
    app.include_router(agents.router)
    app.include_router(memory.router)
    app.include_router(governance.router)
    app.include_router(approvals.router)
    app.include_router(architecture.router)
    app.include_router(workshop.router)
    app.include_router(outputs.router)
    app.include_router(debugging.router)
    app.include_router(replay.router)
    app.include_router(foundry_admin.router)

    return app


app = create_app()

