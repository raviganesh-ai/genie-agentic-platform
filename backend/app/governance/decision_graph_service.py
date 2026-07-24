"""Decision graph service.

Builds and maintains a ``DecisionGraph`` per session (see "DECISION GRAPH"
in the Phase 5 instructions): agent-to-agent dependencies, recommendation
dependencies, and approval dependencies. Held in-process for Phase 5 (no
separate repository - a session's graph is small enough to keep as one
object, mirroring how ``EnterpriseKnowledgeRecord`` etc. are stored whole);
a durable backend is a later-phase concern per
``Settings.lineage_store_backend``.
"""
from __future__ import annotations

from typing import Any

from app.models.decision_graph import (
    DecisionEdge,
    DecisionEdgeType,
    DecisionGraph,
    DecisionNode,
    DecisionNodeType,
)

__all__ = ["DecisionGraphService"]


class DecisionGraphService:
    """Creates and queries per-session ``DecisionGraph`` instances."""

    def __init__(self) -> None:
        self._graphs: dict[str, DecisionGraph] = {}

    def get_graph(self, session_id: str) -> DecisionGraph | None:
        return self._graphs.get(session_id)

    def _graph_for(self, session_id: str) -> DecisionGraph:
        graph = self._graphs.get(session_id)
        if graph is None:
            graph = DecisionGraph(session_id=session_id)
            self._graphs[session_id] = graph
        return graph

    def add_node(
        self,
        *,
        session_id: str,
        node_id: str,
        node_type: DecisionNodeType,
        label: str,
        metadata: dict[str, Any] | None = None,
    ) -> DecisionNode:
        graph = self._graph_for(session_id)
        node = DecisionNode(
            id=node_id,
            node_type=node_type,
            label=label,
            session_id=session_id,
            metadata=metadata or {},
        )
        graph.add_node(node)
        return node

    def add_edge(
        self,
        *,
        session_id: str,
        edge_id: str,
        edge_type: DecisionEdgeType,
        source_id: str,
        target_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> DecisionEdge:
        graph = self._graph_for(session_id)
        edge = DecisionEdge(
            id=edge_id,
            edge_type=edge_type,
            source_id=source_id,
            target_id=target_id,
            session_id=session_id,
            metadata=metadata or {},
        )
        graph.add_edge(edge)
        return edge
