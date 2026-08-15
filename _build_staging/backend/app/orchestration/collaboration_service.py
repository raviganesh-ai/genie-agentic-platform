"""Collaboration service.

Implements the "COLLABORATION MODEL" for Phase 6: agent-to-agent
collaboration, shared memory collaboration, recommendation dependencies,
and approval dependencies. Every recorded ``CollaborationEvent`` is mirrored
into the Phase 5 ``DecisionGraphService`` (unmodified) as nodes/edges so the
Collaboration Graph and Decision Graph stay consistent.
"""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.governance.decision_graph_service import DecisionGraphService
from app.models.collaboration_models import CollaborationEvent, CollaborationType
from app.models.decision_graph import DecisionNodeType

__all__ = ["CollaborationService"]

_EDGE_TYPE_BY_COLLABORATION: dict[CollaborationType, str] = {
    "agent_to_agent": "agent_to_agent",
    "shared_memory": "memory_dependency",
    "recommendation_dependency": "recommendation_dependency",
    "approval_dependency": "approval_dependency",
}


class CollaborationService:
    """Records ``CollaborationEvent``s and mirrors them into the decision graph."""

    def __init__(self, *, decision_graph_service: DecisionGraphService) -> None:
        self._decision_graph_service = decision_graph_service
        self._events: dict[str, list[CollaborationEvent]] = {}

    def record_agent_to_agent(
        self,
        *,
        session_id: str,
        workflow_run_id: str,
        trace_id: str,
        source_agent_id: str,
        target_agent_id: str,
        detail: str = "",
    ) -> CollaborationEvent:
        return self._record(
            collaboration_type="agent_to_agent",
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            trace_id=trace_id,
            source_agent_id=source_agent_id,
            target_agent_id=target_agent_id,
            detail=detail,
        )

    def record_shared_memory_collaboration(
        self,
        *,
        session_id: str,
        workflow_run_id: str,
        trace_id: str,
        source_agent_id: str,
        memory_reference: str,
        detail: str = "",
    ) -> CollaborationEvent:
        return self._record(
            collaboration_type="shared_memory",
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            trace_id=trace_id,
            source_agent_id=source_agent_id,
            memory_reference=memory_reference,
            detail=detail,
        )

    def record_recommendation_dependency(
        self,
        *,
        session_id: str,
        workflow_run_id: str,
        trace_id: str,
        source_agent_id: str,
        recommendation_id: str,
        detail: str = "",
    ) -> CollaborationEvent:
        return self._record(
            collaboration_type="recommendation_dependency",
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            trace_id=trace_id,
            source_agent_id=source_agent_id,
            recommendation_id=recommendation_id,
            detail=detail,
        )

    def record_approval_dependency(
        self,
        *,
        session_id: str,
        workflow_run_id: str,
        trace_id: str,
        source_agent_id: str,
        approval_id: str,
        detail: str = "",
    ) -> CollaborationEvent:
        return self._record(
            collaboration_type="approval_dependency",
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            trace_id=trace_id,
            source_agent_id=source_agent_id,
            approval_id=approval_id,
            detail=detail,
        )

    def events_for_run(self, workflow_run_id: str) -> list[CollaborationEvent]:
        return list(self._events.get(workflow_run_id, []))

    def _record(
        self,
        *,
        collaboration_type: CollaborationType,
        session_id: str,
        workflow_run_id: str,
        trace_id: str,
        source_agent_id: str,
        target_agent_id: str | None = None,
        memory_reference: str | None = None,
        recommendation_id: str | None = None,
        approval_id: str | None = None,
        detail: str = "",
    ) -> CollaborationEvent:
        event = CollaborationEvent(
            id=str(uuid4()),
            collaboration_type=collaboration_type,
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            trace_id=trace_id,
            source_agent_id=source_agent_id,
            target_agent_id=target_agent_id,
            memory_reference=memory_reference,
            recommendation_id=recommendation_id,
            approval_id=approval_id,
            detail=detail,
            timestamp=datetime.now(UTC),
        )
        self._events.setdefault(workflow_run_id, []).append(event)
        self._mirror_into_decision_graph(event)
        return event

    def _mirror_into_decision_graph(self, event: CollaborationEvent) -> None:
        self._ensure_node(
            session_id=event.session_id, node_id=event.source_agent_id, node_type="agent"
        )

        target_node_id: str | None = None
        target_node_type: DecisionNodeType | None = None
        if event.target_agent_id is not None:
            target_node_id, target_node_type = event.target_agent_id, "agent"
        elif event.memory_reference is not None:
            target_node_id, target_node_type = event.memory_reference, "memory_record"
        elif event.recommendation_id is not None:
            target_node_id, target_node_type = event.recommendation_id, "recommendation"
        elif event.approval_id is not None:
            target_node_id, target_node_type = event.approval_id, "approval"

        if target_node_id is None or target_node_type is None:
            return

        self._ensure_node(
            session_id=event.session_id, node_id=target_node_id, node_type=target_node_type
        )
        self._decision_graph_service.add_edge(
            session_id=event.session_id,
            edge_id=event.id,
            edge_type=_EDGE_TYPE_BY_COLLABORATION[event.collaboration_type],
            source_id=event.source_agent_id,
            target_id=target_node_id,
        )

    def _ensure_node(
        self, *, session_id: str, node_id: str, node_type: DecisionNodeType
    ) -> None:
        graph = self._decision_graph_service.get_graph(session_id)
        if graph is not None and any(node.id == node_id for node in graph.nodes):
            return
        self._decision_graph_service.add_node(
            session_id=session_id, node_id=node_id, node_type=node_type, label=node_id
        )
