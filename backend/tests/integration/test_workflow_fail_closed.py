"""Integration tests for Phase 6 fail-closed behavior.

Every scenario here must raise (or return a closed status), never silently
continue or fall back to unsafe behavior, per the "FAIL CLOSED" requirement.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.agents.gateway import UnknownAgentError, create_agent_gateway
from app.agents.registry import AgentRegistry
from app.governance.decision_graph_service import DecisionGraphService
from app.governance.governance_service import GovernanceService, create_governance_service
from app.memory.memory_service import create_memory_service
from app.models.workflow_models import WorkflowStepInput
from app.orchestration.agent_orchestrator import create_agent_orchestrator
from app.orchestration.collaboration_service import CollaborationService
from app.orchestration.handoff_service import HandoffService
from app.orchestration.workflow_checkpoint_service import WorkflowCheckpointService
from app.orchestration.workflow_runtime import (
    ApprovalCapabilityMissingError,
    UnknownWorkflowError,
    WorkflowRuntime,
)
from app.orchestration.workflow_step_executor import (
    MissingMemoryReferenceError,
    WorkflowStepExecutor,
)
from app.prompts.registry import PromptRegistry
from app.workflows.registry import WorkflowRegistry

from ._orchestration_helpers import build_orchestration_settings


@pytest.fixture
def orchestrator(tmp_path: Path):
    settings = build_orchestration_settings(tmp_path / "config")
    return create_agent_orchestrator(settings=settings)


async def test_disabled_agent_causes_workflow_to_fail(orchestrator) -> None:
    with pytest.raises(UnknownAgentError):
        await orchestrator.run_workflow(
            workflow_id="disabled-agent-workflow", session_id="session-1", trace_id="trace-1"
        )


async def test_missing_workflow_definition_raises(orchestrator) -> None:
    with pytest.raises(UnknownWorkflowError):
        await orchestrator.run_workflow(
            workflow_id="no-such-workflow", session_id="session-1", trace_id="trace-1"
        )


async def test_missing_required_memory_reference_raises(orchestrator) -> None:
    with pytest.raises(MissingMemoryReferenceError):
        await orchestrator.run_workflow(
            workflow_id="memory-gated-workflow", session_id="session-1", trace_id="trace-1"
        )


async def test_approval_required_but_no_approval_service_configured_raises(
    tmp_path: Path,
) -> None:
    settings = build_orchestration_settings(tmp_path / "config")
    agent_registry = AgentRegistry.load(settings.agents_path, default_llm=settings.default_llm)
    prompt_registry = PromptRegistry.load(settings.prompts_path)
    workflow_registry = WorkflowRegistry.load(settings.workflows_path)
    agent_gateway = create_agent_gateway(
        settings=settings, agent_registry=agent_registry, prompt_registry=prompt_registry
    )
    governance_service = create_governance_service(settings=settings)
    step_executor = WorkflowStepExecutor(
        agent_registry=agent_registry,
        prompt_registry=prompt_registry,
        agent_gateway=agent_gateway,
        governance_service=governance_service,
    )
    runtime = WorkflowRuntime(
        workflow_registry=workflow_registry,
        step_executor=step_executor,
        handoff_service=HandoffService(governance_service=governance_service),
        collaboration_service=CollaborationService(decision_graph_service=DecisionGraphService()),
        checkpoint_service=WorkflowCheckpointService(),
        approval_service=None,
    )

    with pytest.raises(ApprovalCapabilityMissingError):
        await runtime.run_workflow(
            workflow_id="parallel-workflow",
            session_id="session-1",
            trace_id="trace-1",
            step_inputs={
                "step-a": WorkflowStepInput(step_id="step-a", variables={"x": "1"}),
                "step-b": WorkflowStepInput(step_id="step-b", variables={"y": "2"}),
            },
        )


class _RaisingGovernanceService(GovernanceService):
    """Simulates a governance backend that cannot write events."""

    async def record_execution(self, **kwargs: Any) -> Any:  # type: ignore[override]
        raise RuntimeError("Simulated governance write failure.")


async def test_governance_write_failure_propagates(tmp_path: Path) -> None:
    settings = build_orchestration_settings(tmp_path / "config")
    agent_registry = AgentRegistry.load(settings.agents_path, default_llm=settings.default_llm)
    prompt_registry = PromptRegistry.load(settings.prompts_path)
    agent_gateway = create_agent_gateway(
        settings=settings, agent_registry=agent_registry, prompt_registry=prompt_registry
    )
    memory_service = create_memory_service(settings=settings)
    base_governance_service = create_governance_service(settings=settings)
    raising_governance_service = _RaisingGovernanceService(
        provider=base_governance_service._provider,
        event_repository=base_governance_service._event_repository,
        policy=base_governance_service.policy,
    )

    step_executor = WorkflowStepExecutor(
        agent_registry=agent_registry,
        prompt_registry=prompt_registry,
        agent_gateway=agent_gateway,
        governance_service=raising_governance_service,
        memory_service=memory_service,
    )
    workflow_registry = WorkflowRegistry.load(settings.workflows_path)
    step = workflow_registry.get("parallel-workflow").steps[0]

    with pytest.raises(RuntimeError, match="Simulated governance write failure"):
        await step_executor.execute_step(
            step=step,
            session_id="session-1",
            trace_id="trace-1",
            correlation_id="corr-1",
            step_input=WorkflowStepInput(step_id="step-a", variables={"x": "1"}),
        )
