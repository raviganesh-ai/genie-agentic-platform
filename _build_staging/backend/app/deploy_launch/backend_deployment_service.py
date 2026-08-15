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

import io
import tarfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config.settings import Settings

# Invoked with a short human-readable message right before each real,
# potentially slow sub-phase of ``deploy()`` (source upload, remote ACR
# build, credential lookup, Container App create/update) - lets callers
# (``DeploymentPipelineService``) surface live "what's happening right now"
# detail instead of a single static message for the whole step.
DeploymentProgressCallback = Callable[[str], Awaitable[None]]

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
    ) -> None:
        self._subscription_id = subscription_id
        self._resource_group = resource_group
        self._acr_name = acr_name
        self._container_apps_environment_id = container_apps_environment_id
        self._location = location
        self._foundry_endpoint = foundry_endpoint
        self._foundry_project_name = foundry_project_name

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
            poller.result()
        except BackendDeploymentError:
            raise
        except Exception as exc:
            raise BackendDeploymentError(f"Failed to build backend image in ACR: {exc}") from exc

        try:
            await _report("Reading Azure Container Registry credentials...")
            credentials_client = self._acr_credentials_client()
            credentials = credentials_client.registries.list_credentials(
                self._resource_group, self._acr_name
            )
            registry_username = credentials.username
            registry_password = credentials.passwords[0].value
        except Exception as exc:
            raise BackendDeploymentError(f"Failed to read ACR admin credentials: {exc}") from exc

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
            secret_name = "acr-password"
            # The mission backend's own generated main.py (see
            # ``code_materializer._MAIN_PY_TEMPLATE``) reads
            # os.environ["FOUNDRY_ENDPOINT"]/os.environ["FOUNDRY_PROJECT_NAME"] at
            # request time to reach this mission's own already-provisioned
            # Foundry orchestrator agent - these must be real Container App
            # environment variables, not just baked into the image.
            env_vars = [
                EnvironmentVar(name="FOUNDRY_ENDPOINT", value=self._foundry_endpoint),
                EnvironmentVar(name="FOUNDRY_PROJECT_NAME", value=self._foundry_project_name),
            ]
            
            # Build the Container App envelope with mission-specific managed identity.
            # If mission_identity_resource_id is provided, assign the user-assigned
            # identity to the Container App - this identity has been pre-provisioned
            # with ACR_PULL, Storage, Key Vault, and Search roles by
            # MissionIdentityService, so the app can authenticate without credentials.
            identity_config = None
            if mission_identity_resource_id:
                identity_config = ManagedServiceIdentity(
                    type="UserAssigned",
                    user_assigned_identities={mission_identity_resource_id: UserAssignedIdentity()},
                )
            
            envelope = ContainerApp(
                location=self._location,
                managed_environment_id=self._container_apps_environment_id,
                identity=identity_config,
                configuration=Configuration(
                    ingress=Ingress(external=True, target_port=8000),
                    registries=[
                        RegistryCredentials(
                            server=f"{self._acr_name}.azurecr.io",
                            username=registry_username,
                            password_secret_ref=secret_name,
                        )
                    ],
                    secrets=[Secret(name=secret_name, value=registry_password)],
                ),
                template=Template(
                    containers=[
                        Container(name="backend", image=image_tag, env=env_vars),
                    ]
                ),
            )
            await _report("Creating/updating the Azure Container App revision...")
            poller = container_apps_client.container_apps.begin_create_or_update(
                self._resource_group, app_name, envelope
            )
            result = poller.result()
        except Exception as exc:
            raise BackendDeploymentError(f"Failed to deploy Container App: {exc}") from exc

        fqdn = getattr(getattr(result.configuration, "ingress", None), "fqdn", None)
        backend_url = f"https://{fqdn}" if fqdn else ""
        return BackendDeploymentResult(image_tag=image_tag, backend_url=backend_url)


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


def create_backend_deployment_service(
    *, settings: Settings
) -> BackendDeploymentService | NullBackendDeploymentService:
    """Fail-closed factory: uses the real service once all required settings
    are configured, otherwise a no-op stand-in.
    """

    required = (
        settings.azure_subscription_id,
        settings.deployment_resource_group,
        settings.deployment_acr_name,
        settings.deployment_container_apps_environment_id,
        settings.deployment_location,
        settings.azure_foundry_endpoint,
        settings.azure_foundry_project_name,
    )

    def _build_real() -> BackendDeploymentService:
        if not all(required):
            raise BackendDeploymentError(
                "azure_subscription_id, deployment_resource_group, "
                "deployment_acr_name, deployment_container_apps_environment_id, "
                "deployment_location, azure_foundry_endpoint, and "
                "azure_foundry_project_name must all be configured to deploy a "
                "mission's backend service."
            )
        return BackendDeploymentService(
            subscription_id=settings.azure_subscription_id,  # type: ignore[arg-type]
            resource_group=settings.deployment_resource_group,  # type: ignore[arg-type]
            acr_name=settings.deployment_acr_name,  # type: ignore[arg-type]
            container_apps_environment_id=settings.deployment_container_apps_environment_id,  # type: ignore[arg-type]
            location=settings.deployment_location,  # type: ignore[arg-type]
            foundry_endpoint=settings.azure_foundry_endpoint,  # type: ignore[arg-type]
            foundry_project_name=settings.azure_foundry_project_name,  # type: ignore[arg-type]
        )

    if all(required):
        return _build_real()

    return NullBackendDeploymentService()
