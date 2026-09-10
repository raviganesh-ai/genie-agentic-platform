"""Provisions real per-mission Azure managed identities with least-privilege RBAC roles.

Every deployed mission gets its own brand-new user-assigned managed identity,
separate from Genie's own shared identity. This ensures customer-facing apps
(the mission's backend Container App, the generated specialist agents' own
runtime) can run with genuine least-privilege, and can be deployed to any
Azure subscription without granting the app owner access to Genie's own
infrastructure or other missions' resources.

Each mission identity is granted real Azure RBAC roles scoped to exactly the
resources it needs:
- ``Cognitive Services OpenAI User`` on the mission's own dedicated Foundry
  project (or a shared Foundry account, scoped per mission).
- ``Storage Blob Data Contributor`` on the mission's own ingestion/output
  Storage container.
- ``Key Vault Secrets User`` on the mission's own Key Vault (or a secret
  scoped within Genie's shared Key Vault).
- ``Search Index Data Contributor`` on the mission's own AI Search index
  (or index partition).
- ``AcrPull`` on the mission's Container Registry (for real managed-identity
  ACR authentication, no admin credentials hardcoded).

This service uses the same ``AzureIdentityCredential`` (Genie's own managed
identity) that deployed Genie itself - which holds
``Managed Identity Contributor`` + ``User Access Administrator`` at the
resource group scope, granting it permission to create identities and assign
them roles. Never invents Azure APIs - only uses documented
``azure-mgmt-msi`` and ``azure-mgmt-authorization`` surfaces.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from azure.identity import DefaultAzureCredential
from azure.identity.aio import ManagedIdentityCredential
from azure.mgmt.authorization import AuthorizationManagementClient
from azure.mgmt.msi import ManagedServiceIdentityClient
from azure.mgmt.msi.models import Identity
from azure.mgmt.resource import ResourceManagementClient

from app.config.settings import Settings

__all__ = [
    "AsyncTokenCredential",
    "MissionIdentityProvisioningError",
    "MissionIdentityService",
    "NullMissionIdentityService",
    "ProvisionedMissionIdentity",
    "RoleAssignment",
    "create_mission_identity_service",
    "create_user_assigned_token_credential",
]

# Agent-role mapping: real RBAC role definition ids (built-in Azure roles).
# Scoped per resource - e.g. Foundry account (Cognitive Services OpenAI User),
# Storage container (Storage Blob Data Contributor), etc. - not per-agent tool
# (Genie's per-agent ``allowed_tools`` is finer-grained than RBAC can express).
_ROLE_IDS = {
    "cognitive_services_openai_user": "a97b65f3-24c7-4388-baec-2e87135dc908",
    "storage_blob_data_contributor": "ba92f5b4-2d11-453d-a403-e96b0029c9fe",
    "key_vault_secrets_user": "4633458b-17de-408a-b874-0445c86b69e6",
    "search_index_data_contributor": "8ebe5a00-799e-43f5-93ac-243d3dce84a7",
    "acr_pull": "7f951dda-4ed3-4680-a7ca-43fe172d538d",
}


class MissionIdentityProvisioningError(RuntimeError):
    """Raised when a mission's managed identity or RBAC roles cannot be provisioned."""


class _AccessToken(Protocol):
    token: str


class AsyncTokenCredential(Protocol):
    """Minimal async token contract exposed outside the Azure identity access layer."""

    async def get_token(self, *scopes: str) -> _AccessToken: ...

    async def close(self) -> None: ...


def create_user_assigned_token_credential(*, client_id: str) -> AsyncTokenCredential:
    """Constructs the one configured user-assigned identity credential."""

    return ManagedIdentityCredential(client_id=client_id)


@dataclass(frozen=True)
class RoleAssignment:
    """One real Azure RBAC role granted to a mission identity."""

    role_name: str
    role_definition_id: str
    scope: str
    assignment_id: str | None = None
    provisioned_at: datetime | None = None


@dataclass(frozen=True)
class ProvisionedMissionIdentity:
    """A mission's real managed identity and its RBAC role assignments."""

    mission_id: str
    identity_name: str
    identity_principal_id: str
    identity_client_id: str
    identity_resource_id: str
    role_assignments: list[RoleAssignment]
    provisioned_at: datetime


MissionIdentityProvisionedCallback = Callable[[ProvisionedMissionIdentity], Awaitable[None]]


class MissionIdentityService:
    """Creates real, least-privilege Azure managed identities per mission."""

    def __init__(
        self,
        *,
        settings: Settings,
        subscription_id: str,
        resource_group_name: str,
    ) -> None:
        self._settings = settings
        self._subscription_id = subscription_id
        self._resource_group_name = resource_group_name
        self._credential = DefaultAzureCredential()

    async def provision(
        self,
        *,
        mission_id: str,
        foundry_account_id: str | None = None,
        storage_container_id: str | None = None,
        key_vault_id: str | None = None,
        search_index_id: str | None = None,
        acr_id: str | None = None,
        resource_group_name: str | None = None,
        resource_tags: dict[str, str] | None = None,
        on_identity_provisioned: MissionIdentityProvisionedCallback | None = None,
    ) -> ProvisionedMissionIdentity:
        """Provisions a real managed identity for this mission and assigns RBAC roles.

        Each resource id (foundry_account_id, storage_container_id, etc.) is
        optional - if provided, a real RBAC role is assigned to the mission's
        identity for that resource. If omitted, that role is not assigned
        (mission can still reference the resource by name in backend
        configuration if needed, just without Azure RBAC permission to
        access it - useful for read-only or test scenarios).

        Raises ``MissionIdentityProvisioningError`` if identity creation or
        any role assignment fails (all-or-nothing - partial success rolls back).
        """

        try:
            msi_client = ManagedServiceIdentityClient(self._credential, self._subscription_id)
            authz_client = AuthorizationManagementClient(self._credential, self._subscription_id)
        except Exception as exc:
            raise MissionIdentityProvisioningError(
                f"Cannot provision mission identity: failed to initialize Azure clients: {exc}"
            ) from exc

        identity_name = f"genie-mission-{mission_id[:16]}"
        target_resource_group = resource_group_name or self._resource_group_name
        role_assignments: list[RoleAssignment] = []

        try:
            if resource_group_name is not None:
                ResourceManagementClient(
                    self._credential, self._subscription_id
                ).resource_groups.create_or_update(
                    resource_group_name,
                    {
                        "location": self._settings.deployment_location,
                        "tags": resource_tags or {},
                    },
                )
            # Step 1: Create the user-assigned managed identity.
            identity_resource = Identity(
                location=self._settings.deployment_location,
                tags={"genie-mission-id": mission_id, **(resource_tags or {})},
            )
            identity = msi_client.user_assigned_identities.create_or_update(
                resource_group_name=target_resource_group,
                resource_name=identity_name,
                parameters=identity_resource,
            )
            principal_id = identity.principal_id
            client_id = identity.client_id

            if on_identity_provisioned is not None:
                await on_identity_provisioned(
                    ProvisionedMissionIdentity(
                        mission_id=mission_id,
                        identity_name=identity_name,
                        identity_principal_id=principal_id,
                        identity_client_id=client_id,
                        identity_resource_id=identity.id,
                        role_assignments=[],
                        provisioned_at=datetime.now(UTC),
                    )
                )

            # Step 2: Assign real RBAC roles for each provided resource.
            role_configs = [
                (foundry_account_id, "cognitive_services_openai_user"),
                (storage_container_id, "storage_blob_data_contributor"),
                (key_vault_id, "key_vault_secrets_user"),
                (search_index_id, "search_index_data_contributor"),
                (acr_id, "acr_pull"),
            ]

            for resource_id, role_key in role_configs:
                if not resource_id:
                    continue

                role_def_id = _ROLE_IDS[role_key]
                assignment_name = str(
                    uuid5(NAMESPACE_URL, f"{resource_id}:{principal_id}:{role_def_id}")
                )

                try:
                    assignment = authz_client.role_assignments.create(
                        scope=resource_id,
                        role_assignment_name=assignment_name,
                        parameters={
                            "roleDefinitionId": f"/subscriptions/{self._subscription_id}/providers/Microsoft.Authorization/roleDefinitions/{role_def_id}",
                            "principalId": principal_id,
                            "principalType": "ServicePrincipal",
                        },
                    )
                    role_assignments.append(
                        RoleAssignment(
                            role_name=role_key,
                            role_definition_id=role_def_id,
                            scope=resource_id,
                            assignment_id=assignment.id,
                            provisioned_at=datetime.now(UTC),
                        )
                    )
                except Exception as exc:
                    # If any role assignment fails, roll back the identity and
                    # all successful assignments, then fail closed.
                    try:
                        await self.delete(
                            identity_name=identity_name,
                            principal_id=principal_id,
                            resource_group_name=resource_group_name,
                        )
                    except Exception as cleanup_exc:  # noqa: BLE001 - Azure SDK errors vary.
                        raise MissionIdentityProvisioningError(
                            f"Failed to assign RBAC role '{role_key}' for mission "
                            f"'{mission_id}', and rollback failed: {cleanup_exc}"
                        ) from exc
                    raise MissionIdentityProvisioningError(
                        f"Failed to assign RBAC role '{role_key}' for mission '{mission_id}': {exc}"
                    ) from exc

            return ProvisionedMissionIdentity(
                mission_id=mission_id,
                identity_name=identity_name,
                identity_principal_id=principal_id,
                identity_client_id=client_id,
                identity_resource_id=identity.id,
                role_assignments=role_assignments,
                provisioned_at=datetime.now(UTC),
            )

        except MissionIdentityProvisioningError:
            raise
        except Exception as exc:
            raise MissionIdentityProvisioningError(
                f"Failed to provision mission identity for '{mission_id}': {exc}"
            ) from exc

    async def delete(
        self,
        *,
        identity_name: str,
        principal_id: str,
        resource_group_name: str | None = None,
        role_assignment_ids: list[str] | None = None,
    ) -> None:
        """Deletes one abandoned mission identity and its scoped RBAC assignments."""

        try:
            authorization_client = AuthorizationManagementClient(
                self._credential, self._subscription_id
            )
            if role_assignment_ids is not None:
                assignment_ids = role_assignment_ids
            else:
                assignments = authorization_client.role_assignments.list_for_subscription(
                    filter=f"principalId eq '{principal_id}'"
                )
                assignment_ids = [
                    assignment.id
                    for assignment in assignments
                    if getattr(assignment, "id", None)
                ]
            for assignment_id in assignment_ids:
                try:
                    authorization_client.role_assignments.delete_by_id(assignment_id)
                except Exception as exc:
                    if getattr(exc, "status_code", None) != 404:
                        raise

            identity_client = ManagedServiceIdentityClient(
                self._credential, self._subscription_id
            )
            try:
                poller = identity_client.user_assigned_identities.delete(
                    resource_group_name=resource_group_name or self._resource_group_name,
                    resource_name=identity_name,
                )
                result = getattr(poller, "result", None)
                if callable(result):
                    await asyncio.to_thread(result)
            except Exception as exc:
                if getattr(exc, "status_code", None) != 404:
                    raise
            if resource_group_name is not None:
                group_poller = ResourceManagementClient(
                    self._credential, self._subscription_id
                ).resource_groups.begin_delete(resource_group_name)
                await asyncio.to_thread(group_poller.result)
        except Exception as exc:
            if getattr(exc, "status_code", None) == 404:
                return
            raise MissionIdentityProvisioningError(
                f"Failed to delete mission identity '{identity_name}': {exc}"
            ) from exc


class NullMissionIdentityService:
    """Local-mode stand-in: returns fake identity metadata, makes no Azure calls."""

    async def provision(
        self,
        *,
        mission_id: str,
        foundry_account_id: str | None = None,
        storage_container_id: str | None = None,
        key_vault_id: str | None = None,
        search_index_id: str | None = None,
        acr_id: str | None = None,
        resource_group_name: str | None = None,
        resource_tags: dict[str, str] | None = None,
        on_identity_provisioned: MissionIdentityProvisionedCallback | None = None,
    ) -> ProvisionedMissionIdentity:
        del resource_group_name, resource_tags
        identity_name = f"local-genie-mission-{mission_id[:16]}"
        principal_id = f"local-principal-{mission_id}"
        client_id = f"local-client-{mission_id}"
        resource_id = f"/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/local/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{identity_name}"

        record = ProvisionedMissionIdentity(
            mission_id=mission_id,
            identity_name=identity_name,
            identity_principal_id=principal_id,
            identity_client_id=client_id,
            identity_resource_id=resource_id,
            role_assignments=[],
            provisioned_at=datetime.now(UTC),
        )

        if on_identity_provisioned is not None:
            await on_identity_provisioned(record)

        return record

    async def delete(
        self,
        *,
        identity_name: str,
        principal_id: str,
        resource_group_name: str | None = None,
        role_assignment_ids: list[str] | None = None,
    ) -> None:
        del identity_name, principal_id, resource_group_name, role_assignment_ids


def create_mission_identity_service(
    *,
    settings: Settings,
    subscription_id: str,
    resource_group_name: str,
) -> MissionIdentityService | NullMissionIdentityService:
    """Builds real Azure identity provisioning or fails closed."""

    if not settings.azure_subscription_id:
        raise MissionIdentityProvisioningError(
            "azure_subscription_id must be configured; local/fake mission identity "
            "provisioning is not permitted."
        )
    return MissionIdentityService(
        settings=settings,
        subscription_id=subscription_id,
        resource_group_name=resource_group_name,
    )
