"""Generates the real, deterministic least-access policy for a mission's build.

No LLM involved: every grant in the resulting ``AccessPolicyDocument`` is
read directly off the catalog's own ``AgentDefinition.allowed_tools`` /
``AgentDefinition.memory_access`` fields (``app.agents.registry``,
externally configured under ``config/agents``) - never invented,
summarized, or narrated by an agent.
"""
from __future__ import annotations

from app.agents.registry import AgentRegistry
from app.deploy_launch.models import AccessPolicyDocument, AgentAccessPolicy

__all__ = ["AccessPolicyService"]


class AccessPolicyService:
    """Builds the least-privilege access policy document for a mission."""

    def __init__(self, *, agent_registry: AgentRegistry) -> None:
        self._agent_registry = agent_registry

    def generate(self) -> AccessPolicyDocument:
        """Returns the real, current least-access policy for every enabled agent."""

        agents = [
            AgentAccessPolicy(
                agent_id=agent.id,
                role=agent.role,
                allowed_tools=list(agent.allowed_tools),
                memory_access=list(agent.memory_access),
            )
            for agent in self._agent_registry.list()
            if agent.enabled
        ]
        return AccessPolicyDocument(agents=agents)
