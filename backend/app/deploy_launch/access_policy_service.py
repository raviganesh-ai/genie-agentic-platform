"""Generates the real, deterministic least-access policy for a mission's build.

No LLM involved: every grant in the resulting ``AccessPolicyDocument`` is
read directly off the catalog's own ``AgentDefinition.allowed_tools`` /
``AgentDefinition.memory_access`` fields (``app.agents.registry``,
externally configured under ``config/agents``) - never invented,
summarized, or narrated by an agent.

The mission also receives its own real, brand-new Azure managed identity
with least-privilege RBAC role assignments, provisioned by ``MissionIdentityService``.
"""
from __future__ import annotations

from app.agents.registry import AgentRegistry
from app.deploy_launch.mission_identity_service import (
    MissionIdentityService,
    NullMissionIdentityService,
)
from app.deploy_launch.models import (
    AccessPolicyDocument,
    AgentAccessPolicy,
    MissionIdentityInfo,
)

__all__ = ["AccessPolicyService"]


class AccessPolicyService:
    """Builds the least-privilege access policy document for a mission."""

    def __init__(
        self,
        *,
        agent_registry: AgentRegistry,
        mission_identity_service: MissionIdentityService | NullMissionIdentityService,
        acr_id: str | None = None,
    ) -> None:
        self._agent_registry = agent_registry
        self._mission_identity_service = mission_identity_service
        self._acr_id = acr_id

    async def generate(
        self,
        mission_id: str,
        *,
        resource_group_name: str | None = None,
        resource_tags: dict[str, str] | None = None,
    ) -> AccessPolicyDocument:
        """Returns the real, current least-access policy for every enabled agent.

        Also provisions a real Azure managed identity for this mission and
        includes it in the returned policy document.
        """

        # Provision the real mission identity.
        provisioned_identity = await self._mission_identity_service.provision(
            mission_id=mission_id,
            acr_id=self._acr_id,
            resource_group_name=resource_group_name,
            resource_tags=resource_tags,
        )

        identity_info = MissionIdentityInfo(
            identity_name=provisioned_identity.identity_name,
            identity_principal_id=provisioned_identity.identity_principal_id,
            identity_client_id=provisioned_identity.identity_client_id,
            identity_resource_id=provisioned_identity.identity_resource_id,
            role_assignment_ids=[
                assignment.assignment_id
                for assignment in provisioned_identity.role_assignments
                if assignment.assignment_id is not None
            ],
            assigned_at=provisioned_identity.provisioned_at,
        )

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
        return AccessPolicyDocument(
            agents=agents,
            mission_identity=identity_info,
        )
