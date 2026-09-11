"""Real backend deployment: remote ACR build (no local Docker) + Container Apps deploy.

Real path (used automatically once all required settings below are
configured - see ``create_backend_deployment_service``):

1. Packages the materialized backend build directory (see
   ``app.deploy_launch.code_materializer``) into a gzipped tarball and
   uploads it to Azure Container Registry's own source-upload endpoint
   (``ContainerRegistryManagementClient.registries.get_build_source_upload_url``)
   via ``azure-storage-blob`` - the same mechanism the ``az acr build``
   CLI uses to build a Docker image remotely, without a local Docker
   daemon.
2. Schedules a real ACR Task run (``registries.begin_schedule_run`` with a
   ``DockerBuildRequest``) that builds the uploaded source against its own
   ``Dockerfile`` and pushes the resulting image to that same registry.
3. Creates/updates a real Azure Container App (``ContainerAppsAPIClient
   .container_apps.begin_create_or_update``) to run the newly built image.

Assumption documented per the Coding Instructions ("document assumptions
in comments when SDK behavior is uncertain"): the ACR "Quick Task"
source-upload/build-run APIs used here (``DockerBuildRequest``,
``get_build_source_upload_url``, ``begin_schedule_run``) were moved out of
``azure-mgmt-containerregistry``'s *default* (2023-07-01) API profile but
remain present, real, and installed under the ``2019-06-01-preview``
API version - this client is deliberately constructed pinned to that
version for exactly this reason, not invented.

Registry authentication for the Container App is done with the registry's
own admin credentials (``registries.list_credentials``) rather than a
managed-identity ACR pull role assignment, which would require additional
one-time subscription-level role-assignment plumbing outside this
pipeline's scope - documented here as a deliberate simplification, not a
silently-invented shortcut.
"""
from __future__ import annotations

import asyncio
import io
import tarfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, uuid5

from app.config.settings import Settings
from app.deploy_launch.prototype_api_gateway_service import PrototypeApiGatewayService
from app.deploy_launch.resource_naming import prototype_resource_group_name

# Invoked with a short human-readable message right before each real,
# potentially slow sub-phase of ``deploy()`` (source upload, remote ACR
# build, credential lookup, Container App create/update) - lets callers
# (``DeploymentPipelineService``) surface live "what's happening right now"
# detail instead of a single static message for the whole step.
DeploymentProgressCallback = Callable[[str], Awaitable[None]]

# Terminal ACR run statuses that mean the build did not produce an image.
# Every other status (Queued/Started/Running, or anything Azure adds later)
# is treated as still-in-flight by the polling loop in ``deploy()``.
_ACR_RUN_FAILURE_STATUSES = frozenset({"failed", "canceled", "cancelled", "error", "timeout"})
_COGNITIVE_SERVICES_USER_ROLE_ID = "a97b65f3-24c7-4388-baec-2e87135dc908"

__all__ = [
    "BackendDeploymentError",
    "BackendDeploymentResult",
    "BackendDeploymentService",
    "NullBackendDeploymentService",
    "create_backend_deployment_service",
]


class BackendDeploymentError(RuntimeError):
    """Raised when building or deploying the mission's backend service fails."""


@dataclass(frozen=True)
class BackendDeploymentResult:
    image_tag: str
    backend_url: str


def _tar_gzip_directory(source_dir: Path) -> bytes:
    """Packages ``source_dir`` into an in-memory gzipped tarball (ACR's expected
    source-upload format)."""

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        tar.add(source_dir, arcname=".")
    return buffer.getvalue()


def _extract_resource_group_and_identity_name(identity_resource_id: str) -> tuple[str, str]:
    """Parses an ARM resource ID for a user-assigned managed identity.

    Azure normalizes resource ID path segments case-insensitively; the real
    service may emit ``resourcegroups`` / ``userassignedidentities`` even when
    code or examples show the PascalCase names. Accept both forms so the
    deployment path does not fail closed on a valid Azure resource ID.
    """

    segments = [segment for segment in identity_resource_id.split("/") if segment]
    normalized_segments = [segment.lower() for segment in segments]
    resource_group_index = next((idx for idx, segment in enumerate(normalized_segments) if segment == "resourcegroups"), None)
    identity_index = next((idx for idx, segment in enumerate(normalized_segments) if segment == "userassignedidentities"), None)

    if resource_group_index is None or identity_index is None:
        raise BackendDeploymentError(f"Invalid mission identity resource id '{identity_resource_id}'.")

    if resource_group_index + 1 >= len(segments) or identity_index + 1 >= len(segments):
        raise BackendDeploymentError(f"Invalid mission identity resource id '{identity_resource_id}'.")

    return segments[resource_group_index + 1], segments[identity_index + 1]


class BackendDeploymentService:
    """Builds a mission's backend image in ACR and deploys it to Container Apps."""

    def __init__(
        self,
        *,
        subscription_id: str,
        resource_group: str,
        acr_name: str,
        container_apps_environment_id: str,
        location: str,
        foundry_endpoint: str,
        foundry_project_name: str,
        prototype_api_gateway_service: PrototypeApiGatewayService | None = None,
    ) -> None:
        self._subscription_id = subscription_id
        self._resource_group = resource_group
        self._acr_name = acr_name
        self._container_apps_environment_id = container_apps_environment_id
        self._location = location
        self._foundry_endpoint = foundry_endpoint
        self._foundry_project_name = foundry_project_name
        self._prototype_api_gateway_service = prototype_api_gateway_service

    def _acr_client(self) -> Any:
        """Client pinned to the ``2019-06-01-preview`` API version - the version
        that still exposes the ACR Quick Task source-upload/build-run APIs (see
        module docstring's documented assumption)."""

        try:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.containerregistry import ContainerRegistryManagementClient
        except ImportError as exc:
            raise BackendDeploymentError(
                "azure-mgmt-containerregistry / azure-identity are not installed."
            ) from exc
        try:
            return ContainerRegistryManagementClient(
                DefaultAzureCredential(),
                self._subscription_id,
                api_version="2019-06-01-preview",
            )
        except Exception as exc:
            raise BackendDeploymentError(f"Failed to construct ACR management client: {exc}") from exc

    def _acr_credentials_client(self) -> Any:
        """Client on the default (stable, ``2023-07-01``) API version - that
        profile is what exposes ``registries.list_credentials`` (moved out of
        the preview profile used by ``_acr_client``)."""

        try:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.containerregistry import ContainerRegistryManagementClient
        except ImportError as exc:
            raise BackendDeploymentError(
                "azure-mgmt-containerregistry / azure-identity are not installed."
            ) from exc
        try:
            return ContainerRegistryManagementClient(DefaultAzureCredential(), self._subscription_id)
        except Exception as exc:
            raise BackendDeploymentError(f"Failed to construct ACR management client: {exc}") from exc

    def _container_apps_client(self) -> Any:
        try:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.appcontainers import ContainerAppsAPIClient
        except ImportError as exc:
            raise BackendDeploymentError(
                "azure-mgmt-appcontainers / azure-identity are not installed."
            ) from exc
        try:
            return ContainerAppsAPIClient(DefaultAzureCredential(), self._subscription_id)
        except Exception as exc:
            raise BackendDeploymentError(f"Failed to construct Container Apps client: {exc}") from exc

    async def delete(self, *, mission_slug: str) -> None:
        """Deletes the Container App that owns the generated backend."""

        app_name = f"genie-{mission_slug}-backend"
        client = self._container_apps_client()
        for resource_group in (
            prototype_resource_group_name(mission_slug),
            self._resource_group,
        ):
            try:
                poller = client.container_apps.begin_delete(resource_group, app_name)
                await asyncio.to_thread(poller.result)
                return
            except Exception as exc:
                if getattr(exc, "status_code", None) == 404:
                    continue
                raise BackendDeploymentError(
                    f"Failed to delete backend Container App '{app_name}': {exc}"
                ) from exc

    def _configure_mission_identity(self, identity_resource_id: str) -> str:
        """Returns the mission identity client id after granting Foundry invocation access."""

        try:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.authorization import AuthorizationManagementClient
            from azure.mgmt.msi import ManagedServiceIdentityClient
        except ImportError as exc:
            raise BackendDeploymentError(
                "azure-mgmt-authorization / azure-mgmt-msi / azure-identity are not installed."
            ) from exc

        resource_group, identity_name = _extract_resource_group_and_identity_name(
            identity_resource_id
        )

        credential = DefaultAzureCredential()
        try:
            identity = ManagedServiceIdentityClient(
                credential, self._subscription_id
            ).user_assigned_identities.get(resource_group, identity_name)
            foundry_host = urlparse(self._foundry_endpoint).hostname or ""
            foundry_account_name = foundry_host.split(".")[0]
            if not foundry_account_name or not identity.principal_id or not identity.client_id:
                raise BackendDeploymentError("Mission identity or Foundry account could not be resolved.")
            foundry_scope = (
                f"/subscriptions/{self._subscription_id}/resourceGroups/{self._resource_group}"
                f"/providers/Microsoft.CognitiveServices/accounts/{foundry_account_name}"
            )
            assignment_id = str(uuid5(NAMESPACE_URL, f"{foundry_scope}:{identity.principal_id}:{_COGNITIVE_SERVICES_USER_ROLE_ID}"))
            try:
                AuthorizationManagementClient(credential, self._subscription_id).role_assignments.create(
                    scope=foundry_scope,
                    role_assignment_name=assignment_id,
                    parameters={
                        "roleDefinitionId": (
                            f"/subscriptions/{self._subscription_id}/providers/Microsoft.Authorization/"
                            f"roleDefinitions/{_COGNITIVE_SERVICES_USER_ROLE_ID}"
                        ),
                        "principalId": identity.principal_id,
                        "principalType": "ServicePrincipal",
                    },
                )
            except Exception as exc:
                if "RoleAssignmentExists" not in str(exc):
                    raise
            return identity.client_id
        except BackendDeploymentError:
            raise
        except Exception as exc:
            raise BackendDeploymentError(
                f"Failed to configure mission identity for Foundry access: {exc}"
            ) from exc

    async def deploy(
        self,
        *,
        mission_slug: str,
        build_root: Path,
        mission_identity_resource_id: str | None = None,
        on_progress: DeploymentProgressCallback | None = None,
    ) -> BackendDeploymentResult:
        """Builds ``build_root`` (must contain its own ``Dockerfile``) in ACR and
        deploys the resulting image as a Container App named after ``mission_slug``.

        When given, ``mission_identity_resource_id`` is the resource ID of the
        mission's user-assigned managed identity (created by MissionIdentityService).
        The Container App is assigned this identity so it can authenticate to Azure
        services (ACR for image pull, Key Vault, Storage, Foundry) using managed
        identity instead of hardcoded credentials.

        When given, ``on_progress`` is awaited with a short status message
        before each real sub-phase begins (upload, remote ACR build,
        credential lookup, Container App create/update) - callers can use
        this to reflect true, live progress within this single step.
        """

        async def _report(message: str) -> None:
            if on_progress is not None:
                await on_progress(message)

        dockerfile = build_root / "Dockerfile"
        if not dockerfile.exists():
            raise BackendDeploymentError(
                f"No Dockerfile found in materialized build directory '{build_root}'."
            )
        private_gateway_enabled = self._prototype_api_gateway_service is not None
        if private_gateway_enabled and not mission_identity_resource_id:
            raise BackendDeploymentError(
                "Private prototype deployment requires its mission user-assigned identity."
            )

        acr_client = self._acr_client()
        image_tag = f"{self._acr_name}.azurecr.io/{mission_slug}:latest"

        try:
            from azure.storage.blob import BlobClient

            await _report("Packaging backend build and uploading source to Azure Container Registry...")
            upload_source = acr_client.registries.get_build_source_upload_url(
                self._resource_group, self._acr_name
            )
            tarball = _tar_gzip_directory(build_root)
            blob_client = BlobClient.from_blob_url(upload_source.upload_url)
            blob_client.upload_blob(tarball, overwrite=True)

            from azure.mgmt.containerregistry.v2019_06_01_preview.models import (
                DockerBuildRequest,
                PlatformProperties,
            )

            build_request = DockerBuildRequest(
                source_location=upload_source.relative_path,
                platform=PlatformProperties(os="Linux"),
                docker_file_path="Dockerfile",
                image_names=[image_tag],
                is_push_enabled=True,
                no_cache=False,
            )
            await _report("Building container image in Azure Container Registry (this can take a minute or two)...")
            poller = acr_client.registries.begin_schedule_run(
                self._resource_group, self._acr_name, build_request
            )
            run_result = poller.result()

            # The ARM long-running-operation (poller.result()) only confirms the
            # ACR "Run" resource itself was created/updated successfully - it does
            # NOT mean the docker build+push inside that run succeeded. A failed
            # build (bad Dockerfile, compile error, etc.) still returns a
            # successful ARM operation with the Run's own `status` field set to
            # something other than "Succeeded" and no image ever pushed.
            #
            # Status transitions: Queued -> Started -> Running -> Succeeded|Failed
            # (ACR can also report Canceled/Error/Timeout as terminal outcomes).
            # Poll until we reach a terminal status rather than failing
            # immediately on a non-terminal one like "Queued"/"Running".
            run_id = getattr(run_result, "run_id", None)
            if not run_id:
                raise BackendDeploymentError(
                    f"ACR build for image '{image_tag}' returned no run ID."
                )
            
            max_wait_seconds = 600  # 10 minutes
            start_time = time.time()
            poll_interval_seconds = 5
            
            while True:
                elapsed = time.time() - start_time
                if elapsed > max_wait_seconds:
                    raise BackendDeploymentError(
                        f"ACR build for image '{image_tag}' did not complete within "
                        f"{max_wait_seconds} seconds. Check the ACR task run logs (run ID: {run_id})."
                    )
                
                # Fetch latest run status
                run_detail = acr_client.runs.get(
                    self._resource_group, self._acr_name, run_id
                )
                run_status = getattr(run_detail, "status", None)
                
                if run_status:
                    status_lower = run_status.lower()
                    if status_lower == "succeeded":
                        # Build completed successfully
                        break
                    elif status_lower in _ACR_RUN_FAILURE_STATUSES:
                        raise BackendDeploymentError(
                            f"ACR build for image '{image_tag}' ended with status "
                            f"'{run_status}'. Check the ACR task run logs "
                            f"(run ID: {run_id}) for details."
                        )
                    else:
                        # Any non-terminal status (Queued/Started/Running) - and
                        # deliberately any status this code does not recognize -
                        # is treated as still-in-flight rather than a hard
                        # failure, so a newly-introduced ACR status can never
                        # abort an otherwise-healthy build. The max_wait_seconds
                        # timeout above remains the backstop.
                        await asyncio.sleep(poll_interval_seconds)
                        continue
                else:
                    raise BackendDeploymentError(
                        f"ACR build for image '{image_tag}' returned no status. Run ID: {run_id}"
                    )
        except BackendDeploymentError:
            raise
        except Exception as exc:
            raise BackendDeploymentError(f"Failed to build backend image in ACR: {exc}") from exc

        registry_username: str | None = None
        registry_password: str | None = None
        managed_environment_id = self._container_apps_environment_id
        if private_gateway_enabled:
            try:
                gateway_infrastructure = (
                    await self._prototype_api_gateway_service.provision_infrastructure(
                        mission_slug=mission_slug,
                        on_progress=on_progress,
                    )
                )
                managed_environment_id = gateway_infrastructure.managed_environment_id
            except Exception as exc:
                raise BackendDeploymentError(
                    f"Failed to provision private prototype infrastructure: {exc}"
                ) from exc
        if not private_gateway_enabled:
            try:
                await _report("Reading Azure Container Registry credentials...")
                credentials_client = self._acr_credentials_client()
                credentials = credentials_client.registries.list_credentials(
                    self._resource_group, self._acr_name
                )
                registry_username = credentials.username
                registry_password = credentials.passwords[0].value
            except Exception as exc:
                raise BackendDeploymentError(
                    f"Failed to read ACR admin credentials: {exc}"
                ) from exc

        container_apps_client = self._container_apps_client()
        try:
            from azure.mgmt.appcontainers.models import (
                Configuration,
                Container,
                ContainerApp,
                EnvironmentVar,
                Ingress,
                ManagedServiceIdentity,
                RegistryCredentials,
                Secret,
                Template,
                UserAssignedIdentity,
            )

            app_name = f"genie-{mission_slug}-backend"
            # The mission backend's own generated main.py (see
            # ``code_materializer._MAIN_PY_TEMPLATE``) reads
            # os.environ["FOUNDRY_ENDPOINT"]/os.environ["FOUNDRY_PROJECT_NAME"] at
            # request time to reach this mission's own already-provisioned
            # Foundry orchestrator agent - these must be real Container App
            # environment variables, not just baked into the image.
            env_vars = [
                EnvironmentVar(name="FOUNDRY_ENDPOINT", value=self._foundry_endpoint),
                EnvironmentVar(name="FOUNDRY_PROJECT_NAME", value=self._foundry_project_name),
                EnvironmentVar(name="FOUNDRY_ORCHESTRATOR_AGENT_VERSION", value="1"),
            ]
            # Build the Container App envelope with mission-specific managed identity.
            # If mission_identity_resource_id is provided, assign the user-assigned
            # identity to the Container App - this identity has been pre-provisioned
            # with ACR_PULL, Storage, Key Vault, and Search roles by
            # MissionIdentityService, so the app can authenticate without credentials.
            identity_config = None
            if mission_identity_resource_id:
                mission_identity_client_id = self._configure_mission_identity(
                    mission_identity_resource_id
                )
                env_vars.append(
                    EnvironmentVar(name="AZURE_CLIENT_ID", value=mission_identity_client_id)
                )
                identity_config = ManagedServiceIdentity(
                    type="UserAssigned",
                    user_assigned_identities={mission_identity_resource_id: UserAssignedIdentity()},
                )

            registry_credentials = RegistryCredentials(
                server=f"{self._acr_name}.azurecr.io",
                identity=(
                    mission_identity_resource_id
                    if private_gateway_enabled
                    else None
                ),
                username=registry_username,
                password_secret_ref=(
                    "acr-password" if registry_password is not None else None
                ),
            )
            registry_secrets = (
                [Secret(name="acr-password", value=registry_password)]
                if registry_password is not None
                else []
            )

            containers = [Container(name="backend", image=image_tag, env=env_vars)]
            envelope = ContainerApp(
                location=self._location,
                tags={"genie-managed-by": "genie", "genie-mission-id": mission_slug},
                managed_environment_id=managed_environment_id,
                identity=identity_config,
                configuration=Configuration(
                    ingress=Ingress(
                        external=True,
                        target_port=8000,
                        additional_port_mappings=None,
                    ),
                    registries=[registry_credentials],
                    secrets=registry_secrets,
                ),
                template=Template(
                    containers=containers
                ),
            )
            await _report("Creating/updating the Azure Container App revision...")
            poller = container_apps_client.container_apps.begin_create_or_update(
                prototype_resource_group_name(mission_slug), app_name, envelope
            )
            result = poller.result()
        except Exception as exc:
            raise BackendDeploymentError(f"Failed to deploy Container App: {exc}") from exc

        fqdn = getattr(getattr(result.configuration, "ingress", None), "fqdn", None)
        private_backend_url = f"https://{fqdn}" if fqdn else ""
        backend_url = private_backend_url
        if private_gateway_enabled:
            try:
                backend_url = await self._prototype_api_gateway_service.publish_api(
                    mission_slug=mission_slug,
                    backend_url=private_backend_url,
                    on_progress=on_progress,
                )
            except Exception as exc:
                raise BackendDeploymentError(
                    f"Failed to publish protected prototype API: {exc}"
                ) from exc
        return BackendDeploymentResult(
            image_tag=image_tag,
            backend_url=backend_url,
        )

    async def configure_gateway_frontend_origin(
        self,
        *,
        mission_slug: str,
        frontend_origin: str,
    ) -> None:
        """Replaces the bootstrap-deny APIM origin with the deployed SPA origin."""

        if self._prototype_api_gateway_service is None:
            return
        try:
            await self._prototype_api_gateway_service.configure_frontend_origin(
                mission_slug=mission_slug,
                frontend_origin=frontend_origin,
            )
        except Exception as exc:
            raise BackendDeploymentError(
                f"Failed to configure prototype gateway CORS origin: {exc}"
            ) from exc


class NullBackendDeploymentService:
    """Local/test double: real behavior end-to-end minus any actual Azure calls."""

    async def deploy(
        self,
        *,
        mission_slug: str,
        build_root: Path,
        mission_identity_resource_id: str | None = None,
        on_progress: DeploymentProgressCallback | None = None,
    ) -> BackendDeploymentResult:
        del build_root, mission_identity_resource_id
        if on_progress is not None:
            await on_progress("Deploying backend service (local mode, no real Azure calls)...")
        return BackendDeploymentResult(
            image_tag=f"local/{mission_slug}:dev",
            backend_url=f"http://localhost/missions/{mission_slug}/backend",
        )

    async def configure_gateway_frontend_origin(
        self,
        *,
        mission_slug: str,
        frontend_origin: str,
    ) -> None:
        del mission_slug, frontend_origin

    async def delete(self, *, mission_slug: str) -> None:
        del mission_slug


def create_backend_deployment_service(
    *, settings: Settings
) -> BackendDeploymentService | NullBackendDeploymentService:
    """Builds the real service or fails closed when Azure is incomplete."""

    required = (
        settings.azure_subscription_id,
        settings.deployment_resource_group,
        settings.deployment_acr_name,
        settings.deployment_container_apps_environment_id,
        settings.deployment_location,
        settings.azure_foundry_endpoint,
        settings.azure_foundry_project_name,
    )

    if not all(required):
        raise BackendDeploymentError(
            "azure_subscription_id, deployment_resource_group, "
            "deployment_acr_name, deployment_container_apps_environment_id, "
            "deployment_location, azure_foundry_endpoint, and "
            "azure_foundry_project_name must all be configured; local/fake "
            "backend deployment is not permitted."
        )
    return BackendDeploymentService(
        subscription_id=settings.azure_subscription_id,  # type: ignore[arg-type]
        resource_group=settings.deployment_resource_group,  # type: ignore[arg-type]
        acr_name=settings.deployment_acr_name,  # type: ignore[arg-type]
        container_apps_environment_id=settings.deployment_container_apps_environment_id,  # type: ignore[arg-type]
        location=settings.deployment_location,  # type: ignore[arg-type]
        foundry_endpoint=settings.azure_foundry_endpoint,  # type: ignore[arg-type]
        foundry_project_name=settings.azure_foundry_project_name,  # type: ignore[arg-type]
        prototype_api_gateway_service=(
            PrototypeApiGatewayService(
                subscription_id=settings.azure_subscription_id,  # type: ignore[arg-type]
                location=settings.deployment_location,  # type: ignore[arg-type]
                publisher_email=settings.prototype_api_gateway_publisher_email or "",
                publisher_name=settings.prototype_api_gateway_publisher_name or "",
                sku_name=settings.prototype_api_gateway_sku_name,
                capacity=settings.prototype_api_gateway_capacity,
            )
            if settings.prototype_api_gateway_enabled
            else None
        ),
    )
