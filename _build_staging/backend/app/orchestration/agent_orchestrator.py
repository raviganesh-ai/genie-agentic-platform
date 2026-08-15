"""Agent orchestrator facade.

The single entry point Phase 7 API routes should use to run workflows,
request reanalysis, and handle detected failures. Performs no business
reasoning itself - it only wires the Phase 6 orchestration services
together and exposes a small, stable surface. ``create_agent_orchestrator``
mirrors the ``create_agent_gateway`` / ``create_memory_service`` /
``create_governance_service`` factory pattern established in earlier
phases.
"""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.agents.gateway import AgentGateway, create_agent_gateway
from app.agents.models import AgentExecutionRequest, AgentExecutionResult
from app.agents.registry import AgentRegistry
from app.agents.tools.orchestration_tools import register_orchestrator_delegation_tools
from app.agents.tools.registration import build_default_tool_registry
from app.config.settings import Settings
from app.governance.approval_service import ApprovalService, create_approval_service
from app.governance.decision_graph_service import DecisionGraphService
from app.governance.governance_service import GovernanceService, create_governance_service
from app.governance.recommendation_lineage_service import RecommendationLineageService
from app.memory.memory_service import MemoryService, create_memory_service
from app.models.reanalysis_models import (
    ReanalysisRequest,
    ReanalysisRequestType,
    ReanalysisResult,
)
from app.models.workflow_models import (
    DeliverablePackage,
    DeliverableType,
    WorkflowRunResult,
    WorkflowStepInput,
)
from app.orchestration.collaboration_service import CollaborationService
from app.orchestration.debugging_workflow_service import DebuggingWorkflowService
from app.orchestration.handoff_service import HandoffService
from app.orchestration.reanalysis_service import ReanalysisService
from app.orchestration.workflow_checkpoint_service import WorkflowCheckpointService
from app.orchestration.workflow_event_bus import WorkflowEventBus
from app.orchestration.workflow_execution_service import WorkflowExecutionService
from app.orchestration.workflow_runtime import WorkflowRuntime
from app.orchestration.workflow_step_executor import WorkflowStepExecutor
from app.prompts.registry import PromptRegistry
from app.repositories.recommendation_lineage_repository import (
    InMemoryRecommendationLineageRepository,
)
from app.repositories.workflow_run_repository import (
    InMemoryWorkflowRunRepository,
    WorkflowRunRepository,
)
from app.services.customer_agent_provisioning_service import (
    CustomerAgentProvisioningService,
    NullCustomerAgentProvisioningService,
    ProvisionedAgentRecord,
    create_customer_agent_provisioning_service,
)
from app.workflows.registry import WorkflowRegistry, WorkflowRegistryError

__all__ = ["AgentOrchestrator", "create_agent_orchestrator"]


class AgentOrchestrator:
    """Coordinates workflow execution, reanalysis routing, and failure handling.

    Contains no agent reasoning: every step of every workflow flows through
    ``AgentGateway`` (``AzureAgentGateway`` in production); this class only
    sequences and records that coordination.
    """

    def __init__(
        self,
        *,
        execution_service: WorkflowExecutionService,
        reanalysis_service: ReanalysisService,
        debugging_workflow_service: DebuggingWorkflowService,
        handoff_service: HandoffService,
        collaboration_service: CollaborationService,
        approval_service: ApprovalService,
        checkpoint_service: WorkflowCheckpointService,
        decision_graph_service: DecisionGraphService,
        governance_service: GovernanceService,
        memory_service: MemoryService,
        agent_registry: AgentRegistry,
        workflow_registry: WorkflowRegistry,
        recommendation_lineage_service: RecommendationLineageService,
        workflow_event_bus: WorkflowEventBus,
        agent_gateway: AgentGateway,
        customer_agent_provisioning_service: (
            CustomerAgentProvisioningService | NullCustomerAgentProvisioningService | None
        ) = None,
    ) -> None:
        self._execution_service = execution_service
        self.agent_gateway = agent_gateway
        self._customer_agent_provisioning_service = (
            customer_agent_provisioning_service or NullCustomerAgentProvisioningService()
        )
        self.approval_service = approval_service
        self._reanalysis_service = reanalysis_service
        self._debugging_workflow_service = debugging_workflow_service
        self.handoff_service = handoff_service
        self.collaboration_service = collaboration_service
        self.checkpoint_service = checkpoint_service
        # Exposed (read-only use expected) so Phase 7 API/services can render
        # the architecture decision graph, query governance events, read
        # shared memory, list registered agents, and subscribe to live
        # workflow step events - all against the exact same wired instances
        # this orchestrator uses, rather than constructing separate,
        # inconsistent duplicates.
        self.decision_graph_service = decision_graph_service
        self.governance_service = governance_service
        self.memory_service = memory_service
        self.agent_registry = agent_registry
        self.workflow_registry = workflow_registry
        self.recommendation_lineage_service = recommendation_lineage_service
        self.workflow_event_bus = workflow_event_bus

    async def get_workflow_run(self, workflow_run_id: str) -> WorkflowRunResult | None:
        """Fetches a workflow run's latest durably persisted result, if any.

        Reflects every wave completed so far - including waves from a call
        that is still in flight elsewhere or was interrupted before
        returning - not just the outcome of a call that has fully finished,
        so a stalled/never-resumed run can always be inspected and resumed
        from its last known-good stage.
        """
        return await self._execution_service.get_run(workflow_run_id)

    async def list_workflow_runs(self, session_id: str) -> list[WorkflowRunResult]:
        return await self._execution_service.list_runs_for_session(session_id)

    async def execute_agent(
        self,
        *,
        agent_id: str,
        prompt_id: str,
        variables: dict[str, str],
        session_id: str | None = None,
        trace_id: str | None = None,
    ) -> AgentExecutionResult:
        """Executes one agent directly against one prompt template, outside of
        any workflow step/run.

        Used by the Workshop page's per-component "Regenerate" action: that
        action must revise exactly one already-generated component in
        isolation, never re-run an entire workflow step (which would
        regenerate every other component too). Flows through the exact same
        ``AgentGateway`` (``AzureAgentGateway`` in production) every workflow
        step already executes through - no separate/duplicate execution path.
        """
        request = AgentExecutionRequest(
            agent_id=agent_id,
            prompt_id=prompt_id,
            variables=variables,
            correlation_id=trace_id or str(uuid4()),
            session_id=session_id,
        )
        return await self.agent_gateway.execute(request)

    async def provision_customer_agents(
        self, *, session_id: str, scope_id: str | None = None, trace_id: str | None = None
    ) -> list[ProvisionedAgentRecord]:
        """Provision a dedicated Foundry agent fleet for one customer session (or scope).

        Idempotent - see ``CustomerAgentProvisioningService.provision_for_session``.
        ``scope_id``, when supplied (e.g. a requirement group id), provisions
        a fleet dedicated to that scope instead of the whole session.
        """

        return await self._customer_agent_provisioning_service.provision_for_session(
            session_id=session_id, scope_id=scope_id, trace_id=trace_id
        )

    async def deprovision_customer_agents(
        self, *, session_id: str, scope_id: str | None = None, trace_id: str | None = None
    ) -> None:
        """Tear down a customer session's (or scope's) dedicated Foundry agent fleet, if any."""

        await self._customer_agent_provisioning_service.deprovision_for_session(
            session_id=session_id, scope_id=scope_id, trace_id=trace_id
        )

    def customer_agents_provisioned(self, session_id: str, *, scope_id: str | None = None) -> bool:
        return self._customer_agent_provisioning_service.is_provisioned(session_id, scope_id=scope_id)

    def provisioned_customer_agent_count(
        self, session_id: str, *, scope_id: str | None = None
    ) -> int:
        return len(
            self._customer_agent_provisioning_service.provisioned_agents(
                session_id, scope_id=scope_id
            )
        )

    async def run_workflow(
        self,
        *,
        workflow_id: str,
        session_id: str,
        trace_id: str | None = None,
        step_inputs: dict[str, WorkflowStepInput] | None = None,
        transcript_text: str = "",
        agent_scope_id: str | None = None,
    ) -> WorkflowRunResult:
        return await self._execution_service.start_workflow(
            workflow_id=workflow_id,
            session_id=session_id,
            trace_id=trace_id or str(uuid4()),
            step_inputs=step_inputs,
            transcript_text=transcript_text,
            agent_scope_id=agent_scope_id,
        )

    async def resume_workflow(
        self,
        *,
        workflow_run_id: str,
        session_id: str,
        trace_id: str | None = None,
        step_inputs: dict[str, WorkflowStepInput] | None = None,
        transcript_text: str = "",
    ) -> WorkflowRunResult:
        return await self._execution_service.resume_workflow(
            workflow_run_id=workflow_run_id,
            session_id=session_id,
            trace_id=trace_id or str(uuid4()),
            step_inputs=step_inputs,
            transcript_text=transcript_text,
        )

    async def start_workflow_background(
        self,
        *,
        workflow_id: str,
        session_id: str,
        trace_id: str | None = None,
        step_inputs: dict[str, WorkflowStepInput] | None = None,
        transcript_text: str = "",
        agent_scope_id: str | None = None,
    ) -> WorkflowRunResult:
        """Starts a run without waiting for it to finish.

        Used by the HTTP API, whose requests cannot outlive the ingress
        timeout. Server-side callers that genuinely need the final outcome
        keep using ``run_workflow``.
        """
        return await self._execution_service.start_workflow_background(
            workflow_id=workflow_id,
            session_id=session_id,
            trace_id=trace_id or str(uuid4()),
            step_inputs=step_inputs,
            transcript_text=transcript_text,
            agent_scope_id=agent_scope_id,
        )

    async def resume_workflow_background(
        self,
        *,
        workflow_run_id: str,
        session_id: str,
        trace_id: str | None = None,
        step_inputs: dict[str, WorkflowStepInput] | None = None,
        transcript_text: str = "",
    ) -> WorkflowRunResult:
        """Resumes a run without waiting for it to finish - see
        ``start_workflow_background``.
        """
        return await self._execution_service.resume_workflow_background(
            workflow_run_id=workflow_run_id,
            session_id=session_id,
            trace_id=trace_id or str(uuid4()),
            step_inputs=step_inputs,
            transcript_text=transcript_text,
        )

    def request_reanalysis(
        self,
        *,
        session_id: str,
        workflow_run_id: str,
        trace_id: str,
        requested_by: str,
        request_type: ReanalysisRequestType,
        target_recommendation_id: str | None = None,
        rationale: str = "",
    ) -> ReanalysisResult:
        request = ReanalysisRequest(
            id=str(uuid4()),
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            trace_id=trace_id,
            requested_by=requested_by,
            request_type=request_type,
            target_recommendation_id=target_recommendation_id,
            rationale=rationale,
            requested_at=datetime.now(UTC),
        )
        return self._reanalysis_service.route(request)

    async def handle_failure(
        self, *, session_id: str, source_agent_id: str, error: str, trace_id: str | None = None
    ) -> WorkflowRunResult:
        return await self._debugging_workflow_service.handle_failure(
            session_id=session_id, source_agent_id=source_agent_id, error=error, trace_id=trace_id
        )

    def generate_deliverable_package(
        self,
        *,
        workflow_run_result: WorkflowRunResult,
        deliverable_type: DeliverableType,
    ) -> DeliverablePackage:
        """Assembles a deliverable package from a completed workflow run's step outputs.

        The orchestrator performs no content generation here: every section's
        text is exactly the ``output_text`` an Azure-hosted agent already
        produced during ``run_workflow``.
        """

        sections = {
            result.step_id: result.output_text or ""
            for result in workflow_run_result.step_results
            if result.status == "completed"
        }
        return DeliverablePackage(
            id=str(uuid4()),
            session_id=workflow_run_result.session_id,
            workflow_run_id=workflow_run_result.workflow_run_id,
            deliverable_type=deliverable_type,
            generated_at=datetime.now(UTC),
            sections=sections,
        )


def create_agent_orchestrator(
    *,
    settings: Settings,
    agent_gateway: AgentGateway | None = None,
    governance_service: GovernanceService | None = None,
    memory_service: MemoryService | None = None,
    approval_service: ApprovalService | None = None,
    workflow_event_bus: WorkflowEventBus | None = None,
    workflow_run_repository: WorkflowRunRepository | None = None,
) -> AgentOrchestrator:
    """Build an ``AgentOrchestrator`` wired to the externally configured registries.

    Fails closed (raises) if the agent, workflow, or prompt registries, or
    any required policy configuration, is missing or invalid - propagated
    from the underlying ``AgentRegistry.load`` / ``WorkflowRegistry.load`` /
    ``PromptRegistry.load`` / ``create_governance_service`` /
    ``create_memory_service`` / ``create_approval_service`` calls.
    """

    agent_registry = AgentRegistry.load(settings.agents_path, default_llm=settings.default_llm)
    workflow_registry = WorkflowRegistry.load(settings.workflows_path)
    reference_errors = workflow_registry.validate_agent_references(agent_registry)
    if reference_errors:
        raise WorkflowRegistryError("; ".join(reference_errors))
    prompt_registry = PromptRegistry.load(settings.prompts_path)

    resolved_governance_service = governance_service or create_governance_service(
        settings=settings
    )
    resolved_memory_service = memory_service or create_memory_service(settings=settings)
    resolved_approval_service = approval_service or create_approval_service(
        settings=settings, governance_service=resolved_governance_service
    )
    customer_agent_provisioning_service = create_customer_agent_provisioning_service(
        settings=settings,
        agent_registry=agent_registry,
        governance_service=resolved_governance_service,
    )
    tool_registry = build_default_tool_registry(
        memory_service=resolved_memory_service,
        governance_service=resolved_governance_service,
    )
    resolved_agent_gateway = agent_gateway or create_agent_gateway(
        settings=settings,
        agent_registry=agent_registry,
        prompt_registry=prompt_registry,
        session_agent_resolver=customer_agent_provisioning_service,
        tool_registry=tool_registry,
    )
    # Constructed here (rather than further below, where it was previously
    # first needed) so register_orchestrator_delegation_tools can also
    # publish live step_delta events for a delegated specialist's own real
    # streamed output - see that function's event_bus parameter.
    resolved_workflow_event_bus = workflow_event_bus or WorkflowEventBus()
    # Populates the same tool_registry instance already handed to
    # resolved_agent_gateway above (see register_orchestrator_delegation_
    # tools's docstring for why this ordering, rather than a circular
    # construction dependency, is safe) so genie-orchestrator's phase runs
    # can delegate to each connected specialist through this very gateway.
    register_orchestrator_delegation_tools(
        tool_registry,
        agent_gateway=resolved_agent_gateway,
        governance_service=resolved_governance_service,
        agent_registry=agent_registry,
        memory_service=resolved_memory_service,
        event_bus=resolved_workflow_event_bus,
    )
    recommendation_lineage_service = RecommendationLineageService(
        InMemoryRecommendationLineageRepository(), governance_service=resolved_governance_service
    )

    step_executor = WorkflowStepExecutor(
        agent_registry=agent_registry,
        prompt_registry=prompt_registry,
        agent_gateway=resolved_agent_gateway,
        governance_service=resolved_governance_service,
        memory_service=resolved_memory_service,
        event_bus=resolved_workflow_event_bus,
    )
    handoff_service = HandoffService(governance_service=resolved_governance_service)
    decision_graph_service = DecisionGraphService()
    collaboration_service = CollaborationService(decision_graph_service=decision_graph_service)
    checkpoint_service = WorkflowCheckpointService()

    runtime = WorkflowRuntime(
        workflow_registry=workflow_registry,
        step_executor=step_executor,
        handoff_service=handoff_service,
        collaboration_service=collaboration_service,
        checkpoint_service=checkpoint_service,
        approval_service=resolved_approval_service,
    )
    execution_service = WorkflowExecutionService(
        runtime=runtime,
        repository=workflow_run_repository or InMemoryWorkflowRunRepository(),
    )
    reanalysis_service = ReanalysisService(agent_registry=agent_registry)
    debugging_workflow_service = DebuggingWorkflowService(
        settings=settings, execution_service=execution_service
    )

    return AgentOrchestrator(
        execution_service=execution_service,
        reanalysis_service=reanalysis_service,
        debugging_workflow_service=debugging_workflow_service,
        handoff_service=handoff_service,
        collaboration_service=collaboration_service,
        approval_service=resolved_approval_service,
        checkpoint_service=checkpoint_service,
        decision_graph_service=decision_graph_service,
        governance_service=resolved_governance_service,
        memory_service=resolved_memory_service,
        agent_registry=agent_registry,
        workflow_registry=workflow_registry,
        recommendation_lineage_service=recommendation_lineage_service,
        workflow_event_bus=resolved_workflow_event_bus,
        agent_gateway=resolved_agent_gateway,
        customer_agent_provisioning_service=customer_agent_provisioning_service,
    )
