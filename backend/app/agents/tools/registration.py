"""Single wiring entry point for Genie's default ``AgentToolRegistry``.

Mirrors the ``create_agent_gateway``/``create_memory_service`` factory
pattern: ``build_default_tool_registry`` binds every agent's real function
tools onto the concrete domain service instances already constructed for
this process (never separate, inconsistent duplicates) - see
``create_agent_orchestrator`` for the single call site.
"""
from __future__ import annotations

from app.agents.tool_execution import AgentToolRegistry
from app.agents.tools.architecture_tools import register_architecture_tools
from app.agents.tools.build_tools import register_build_tools
from app.agents.tools.debugging_tools import register_debugging_tools
from app.agents.tools.peer_review_tools import register_peer_review_tools
from app.agents.tools.requirements_tools import register_requirements_tools
from app.governance.governance_service import GovernanceService
from app.memory.memory_service import MemoryService

__all__ = ["build_default_tool_registry"]


def build_default_tool_registry(
    *,
    memory_service: MemoryService,
    governance_service: GovernanceService,
) -> AgentToolRegistry:
    """Build the ``AgentToolRegistry`` covering every agent's real tools.

    ``genie-orchestrator``'s own delegation tools (``call_<agent>``) are
    deliberately not registered here, since they need a fully constructed
    ``AgentGateway`` (not yet available at this point in
    ``create_agent_orchestrator``) - see
    ``app.agents.tools.orchestration_tools.register_orchestrator_delegation_
    tools``, called separately once the gateway exists.
    """

    registry = AgentToolRegistry()
    register_requirements_tools(registry, memory_service=memory_service)
    register_architecture_tools(registry, memory_service=memory_service)
    register_build_tools(registry, memory_service=memory_service)
    register_peer_review_tools(registry, governance_service=governance_service)
    register_debugging_tools(registry, governance_service=governance_service)
    return registry
