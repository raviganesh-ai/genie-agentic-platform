"""Unit tests for DecisionGraphService and the DecisionGraph domain model."""
from __future__ import annotations

import pytest

from app.governance.decision_graph_service import DecisionGraphService
from app.models.decision_graph import DecisionEdge, DecisionGraph, DecisionNode


def test_get_graph_returns_none_before_any_node_added():
    service = DecisionGraphService()
    assert service.get_graph("session-1") is None


def test_add_node_and_edge_builds_graph():
    service = DecisionGraphService()

    service.add_node(
        session_id="session-1", node_id="agent-a", node_type="agent", label="Requirements Analyst"
    )
    service.add_node(
        session_id="session-1", node_id="rec-1", node_type="recommendation", label="Use event-driven design"
    )
    service.add_edge(
        session_id="session-1",
        edge_id="edge-1",
        edge_type="recommendation_dependency",
        source_id="agent-a",
        target_id="rec-1",
    )

    graph = service.get_graph("session-1")
    assert graph is not None
    assert {node.id for node in graph.nodes} == {"agent-a", "rec-1"}
    assert len(graph.edges) == 1
    assert graph.edges[0].source_id == "agent-a"
    assert graph.edges[0].target_id == "rec-1"


def test_add_edge_to_unknown_node_raises():
    service = DecisionGraphService()
    service.add_node(session_id="session-1", node_id="agent-a", node_type="agent", label="Agent A")

    with pytest.raises(ValueError):
        service.add_edge(
            session_id="session-1",
            edge_id="edge-1",
            edge_type="agent_to_agent",
            source_id="agent-a",
            target_id="unknown-node",
        )


def test_add_duplicate_node_raises():
    service = DecisionGraphService()
    service.add_node(session_id="session-1", node_id="agent-a", node_type="agent", label="Agent A")

    with pytest.raises(ValueError):
        service.add_node(
            session_id="session-1", node_id="agent-a", node_type="agent", label="Agent A (dup)"
        )


def test_decision_graph_model_add_node_and_edge_directly():
    graph = DecisionGraph(session_id="session-1")
    graph.add_node(DecisionNode(id="a", node_type="agent", label="A", session_id="session-1"))
    graph.add_node(DecisionNode(id="b", node_type="approval", label="B", session_id="session-1"))
    graph.add_edge(
        DecisionEdge(
            id="e1", edge_type="approval_dependency", source_id="a", target_id="b", session_id="session-1"
        )
    )
    assert len(graph.nodes) == 2
    assert len(graph.edges) == 1


def test_decision_graph_model_rejects_edge_to_missing_node():
    graph = DecisionGraph(session_id="session-1")
    graph.add_node(DecisionNode(id="a", node_type="agent", label="A", session_id="session-1"))

    with pytest.raises(ValueError):
        graph.add_edge(
            DecisionEdge(
                id="e1", edge_type="agent_to_agent", source_id="a", target_id="missing", session_id="session-1"
            )
        )
