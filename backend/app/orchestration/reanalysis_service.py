"""Reanalysis routing service.

Implements "REANALYSIS" for Phase 6: routes a challenge/redesign request to
an appropriate registered agent by matching the request type against
externally configured ``AgentDefinition.capabilities`` (never a hardcoded
agent id). This service only *routes* work - it performs no redesign
reasoning itself; the routed-to agent's actual execution happens via
``WorkflowStepExecutor``/``AzureAgentGateway``.
"""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.agents.registry import AgentRegistry
from app.models.reanalysis_models import (
    ReanalysisRequest,
    ReanalysisRequestType,
    ReanalysisResult,
)

__all__ = ["ReanalysisRoutingError", "ReanalysisService"]

# Maps each reanalysis request type to the registered agent capability
# required to service it. This is a routing policy, not an agent
# definition or prompt template: the concrete agent that satisfies a given
# capability is still resolved exclusively from the externally configured
# AgentRegistry (config/agents/registry.yaml), never hardcoded here.
_REQUIRED_CAPABILITY_BY_REQUEST_TYPE: dict[ReanalysisRequestType, str] = {
    "challenge_recommendation": "requirement_extraction",
    "modify_priorities": "requirement_extraction",
    "request_alternative_architecture": "architecture_generation",
    "lower_cost_redesign": "alternative_design_generation",
    "higher_security_redesign": "alternative_design_generation",
    "mvp_redesign": "alternative_design_generation",
    "fabric_first_redesign": "alternative_design_generation",
}


class ReanalysisRoutingError(RuntimeError):
    """Raised when no enabled registered agent can service a reanalysis request."""


class ReanalysisService:
    """Routes ``ReanalysisRequest``s to a capable, enabled registered agent."""

    def __init__(self, *, agent_registry: AgentRegistry) -> None:
        self._agent_registry = agent_registry

    def route(self, request: ReanalysisRequest) -> ReanalysisResult:
        required_capability = _REQUIRED_CAPABILITY_BY_REQUEST_TYPE[request.request_type]

        for agent in self._agent_registry.list():
            if agent.enabled and required_capability in agent.capabilities:
                return ReanalysisResult(
                    id=str(uuid4()),
                    reanalysis_request_id=request.id,
                    routed_to_agent_id=agent.id,
                    status="routed",
                    detail=f"Matched capability '{required_capability}'.",
                    created_at=datetime.now(UTC),
                )

        raise ReanalysisRoutingError(
            f"No enabled agent with capability '{required_capability}' is registered "
            f"to service reanalysis request type '{request.request_type}'."
        )
