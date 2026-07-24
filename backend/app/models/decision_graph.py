"""Decision graph domain model.

Supports the "DECISION GRAPH" requirement for Phase 5: agent-to-agent
dependencies, recommendation dependencies, and approval dependencies,
rendered by the frontend Collaboration Graph (React Flow) in a later
phase.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DecisionNodeType = Literal["agent", "recommendation", "approval", "memory_record", "evidence"]

DecisionEdgeType = Literal[
    "agent_to_agent",
    "recommendation_dependency",
    "approval_dependency",
    "evidence_dependency",
    "memory_dependency",
]

__all__ = ["DecisionEdge", "DecisionEdgeType", "DecisionGraph", "DecisionNode", "DecisionNodeType"]


class DecisionNode(BaseModel):
    """A single node in a session's decision graph."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    node_type: DecisionNodeType
    label: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DecisionEdge(BaseModel):
    """A single directed dependency between two decision graph nodes."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    edge_type: DecisionEdgeType
    source_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DecisionGraph(BaseModel):
    """The full decision graph (nodes + edges) for a single session.

    ``add_node``/``add_edge`` enforce basic graph integrity (no duplicate
    node ids, no edge referencing a node that does not exist) so a
    malformed graph can never be assembled or replayed.
    """

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1)
    nodes: list[DecisionNode] = Field(default_factory=list)
    edges: list[DecisionEdge] = Field(default_factory=list)

    def add_node(self, node: DecisionNode) -> None:
        if any(existing.id == node.id for existing in self.nodes):
            raise ValueError(f"Duplicate decision graph node id '{node.id}'.")
        self.nodes.append(node)

    def add_edge(self, edge: DecisionEdge) -> None:
        node_ids = {node.id for node in self.nodes}
        if edge.source_id not in node_ids or edge.target_id not in node_ids:
            raise ValueError(
                f"Decision graph edge '{edge.id}' references a node that does "
                f"not exist in this graph."
            )
        self.edges.append(edge)
