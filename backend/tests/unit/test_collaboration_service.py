"""Unit tests for the Phase 6 collaboration service."""
from __future__ import annotations

import pytest

from app.governance.decision_graph_service import DecisionGraphService
from app.orchestration.collaboration_service import CollaborationService


@pytest.fixture
def collaboration_service() -> CollaborationService:
    return CollaborationService(decision_graph_service=DecisionGraphService())


def test_record_agent_to_agent_mirrors_into_decision_graph(
    collaboration_service: CollaborationService,
) -> None:
    event = collaboration_service.record_agent_to_agent(
        session_id="session-1",
        workflow_run_id="run-1",
        trace_id="trace-1",
        source_agent_id="agent-a",
        target_agent_id="agent-b",
        detail="Parallel execution in wave 0.",
    )

    assert event.collaboration_type == "agent_to_agent"
    events = collaboration_service.events_for_run("run-1")
    assert events == [event]


def test_record_shared_memory_collaboration_creates_memory_node(
    collaboration_service: CollaborationService,
) -> None:
    collaboration_service.record_shared_memory_collaboration(
        session_id="session-1",
        workflow_run_id="run-1",
        trace_id="trace-1",
        source_agent_id="agent-a",
        memory_reference="requirements-summary",
    )

    graph = collaboration_service._decision_graph_service.get_graph("session-1")
    assert graph is not None
    node_ids = {node.id for node in graph.nodes}
    assert {"agent-a", "requirements-summary"}.issubset(node_ids)
    assert any(edge.edge_type == "memory_dependency" for edge in graph.edges)


def test_record_recommendation_and_approval_dependencies(
    collaboration_service: CollaborationService,
) -> None:
    rec_event = collaboration_service.record_recommendation_dependency(
        session_id="session-1",
        workflow_run_id="run-1",
        trace_id="trace-1",
        source_agent_id="agent-a",
        recommendation_id="rec-1",
    )
    approval_event = collaboration_service.record_approval_dependency(
        session_id="session-1",
        workflow_run_id="run-1",
        trace_id="trace-1",
        source_agent_id="agent-a",
        approval_id="approval-1",
    )

    assert rec_event.recommendation_id == "rec-1"
    assert approval_event.approval_id == "approval-1"
    graph = collaboration_service._decision_graph_service.get_graph("session-1")
    assert graph is not None
    edge_types = {edge.edge_type for edge in graph.edges}
    assert edge_types == {"recommendation_dependency", "approval_dependency"}


def test_repeated_collaboration_with_same_agent_does_not_duplicate_node(
    collaboration_service: CollaborationService,
) -> None:
    collaboration_service.record_agent_to_agent(
        session_id="session-1",
        workflow_run_id="run-1",
        trace_id="trace-1",
        source_agent_id="agent-a",
        target_agent_id="agent-b",
    )
    # A second collaboration reusing "agent-a" must not raise (idempotent node add).
    collaboration_service.record_agent_to_agent(
        session_id="session-1",
        workflow_run_id="run-1",
        trace_id="trace-1",
        source_agent_id="agent-a",
        target_agent_id="agent-c",
    )

    graph = collaboration_service._decision_graph_service.get_graph("session-1")
    assert graph is not None
    assert len([node for node in graph.nodes if node.id == "agent-a"]) == 1
