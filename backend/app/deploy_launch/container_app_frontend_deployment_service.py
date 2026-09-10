"""Deploy generated mission frontends as isolated Azure Container Apps.

Each mission receives its own small Nginx Container App. The generated static
files are built into an image by ACR, so this path does not depend on Blob
Storage network access or static website configuration.
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

from app.config.settings import Settings
from app.deploy_launch.resource_naming import prototype_resource_group_name

DeploymentProgressCallback = Callable[[str], Awaitable[None]]
_ACR_RUN_FAILURE_STATUSES = frozenset({"failed", "canceled", "cancelled", "error", "timeout"})
_FRONTEND_DOCKERFILE = """FROM node:22-alpine AS build
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
"""

__all__ = [
    "ContainerAppFrontendDeploymentError",
    "ContainerAppFrontendDeploymentResult",
    "ContainerAppFrontendDeploymentService",
    "NullContainerAppFrontendDeploymentService",
    "create_container_app_frontend_deployment_service",
]


class ContainerAppFrontendDeploymentError(RuntimeError):
    """Raised when a generated frontend cannot be built or deployed."""


@dataclass(frozen=True)
class ContainerAppFrontendDeploymentResult:
    frontend_url: str
    image_tag: str


def _tar_gzip_directory(source_dir: Path) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        archive.add(source_dir, arcname=".")
    return buffer.getvalue()


class ContainerAppFrontendDeploymentService:
    """Builds and deploys one isolated static frontend Container App per mission."""

    def __init__(
        self,
        *,
        subscription_id: str,
        resource_group: str,
        acr_name: str,
        container_apps_environment_id: str,
        location: str,
    ) -> None:
        self._subscription_id = subscription_id
        self._resource_group = resource_group
        self._acr_name = acr_name
        self._container_apps_environment_id = container_apps_environment_id
        self._location = location

    def _acr_client(self) -> Any:
        try:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.containerregistry import ContainerRegistryManagementClient
            return ContainerRegistryManagementClient(
                DefaultAzureCredential(),
                self._subscription_id,
                api_version="2019-06-01-preview",
            )
        except ImportError as exc:
            raise ContainerAppFrontendDeploymentError(
                "azure-mgmt-containerregistry / azure-identity are not installed."
            ) from exc
        except Exception as exc:
            raise ContainerAppFrontendDeploymentError(
                f"Failed to construct ACR management client: {exc}"
            ) from exc

    def _container_apps_client(self) -> Any:
        try:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.appcontainers import ContainerAppsAPIClient
            return ContainerAppsAPIClient(DefaultAzureCredential(), self._subscription_id)
        except ImportError as exc:
            raise ContainerAppFrontendDeploymentError(
                "azure-mgmt-appcontainers / azure-identity are not installed."
            ) from exc
        except Exception as exc:
            raise ContainerAppFrontendDeploymentError(
                f"Failed to construct Container Apps client: {exc}"
            ) from exc

    async def deploy(
        self,
        *,
        mission_slug: str,
        ui_root: Path,
        app_name: str | None = None,
        mission_identity_resource_id: str | None = None,
        on_progress: DeploymentProgressCallback | None = None,
    ) -> ContainerAppFrontendDeploymentResult:
        async def report(message: str) -> None:
            if on_progress is not None:
                await on_progress(message)

        if not ui_root.exists() or not any(ui_root.iterdir()):
            raise ContainerAppFrontendDeploymentError(
                f"No materialized UI build found at '{ui_root}'."
            )
        if not mission_identity_resource_id:
            raise ContainerAppFrontendDeploymentError(
                "Frontend deployment requires the mission user-assigned identity."
            )

        dockerfile = ui_root / "Dockerfile"
        dockerfile.write_text(_FRONTEND_DOCKERFILE, encoding="utf-8")
        app_name = app_name or f"genie-{mission_slug}-frontend"
        image_tag = f"{self._acr_name}.azurecr.io/{app_name}:latest"

        try:
            acr_client = self._acr_client()
            await report("Packaging frontend and uploading source to Azure Container Registry...")
            upload_source = acr_client.registries.get_build_source_upload_url(
                self._resource_group, self._acr_name
            )
            from azure.storage.blob import BlobClient

            BlobClient.from_blob_url(upload_source.upload_url).upload_blob(
                _tar_gzip_directory(ui_root), overwrite=True
            )

            from azure.mgmt.containerregistry.v2019_06_01_preview.models import (
                DockerBuildRequest,
                PlatformProperties,
            )

            await report("Building frontend container image in Azure Container Registry...")
            build = DockerBuildRequest(
                source_location=upload_source.relative_path,
                platform=PlatformProperties(os="Linux"),
                docker_file_path="Dockerfile",
                image_names=[image_tag],
                is_push_enabled=True,
                no_cache=False,
            )
            run = acr_client.registries.begin_schedule_run(
                self._resource_group, self._acr_name, build
            ).result()
            run_id = getattr(run, "run_id", None)
            if not run_id:
                raise ContainerAppFrontendDeploymentError("ACR frontend build returned no run ID.")

            started = time.time()
            while time.time() - started <= 600:
                detail = acr_client.runs.get(self._resource_group, self._acr_name, run_id)
                status = (getattr(detail, "status", None) or "").lower()
                if status == "succeeded":
                    break
                if status in _ACR_RUN_FAILURE_STATUSES:
                    raise ContainerAppFrontendDeploymentError(
                        f"ACR frontend build ended with status '{status}'."
                    )
                if not status:
                    raise ContainerAppFrontendDeploymentError(
                        f"ACR frontend build returned no status. Run ID: {run_id}"
                    )
                await asyncio.sleep(5)
            else:
                raise ContainerAppFrontendDeploymentError("ACR frontend build timed out after 600 seconds.")
        except ContainerAppFrontendDeploymentError:
            raise
        except Exception as exc:
            raise ContainerAppFrontendDeploymentError(
                f"Failed to build frontend image in ACR: {exc}"
            ) from exc

        try:
            from azure.mgmt.appcontainers.models import (
                Configuration,
                Container,
                ContainerApp,
                Ingress,
                ManagedServiceIdentity,
                RegistryCredentials,
                Scale,
                Template,
                UserAssignedIdentity,
            )

            envelope = ContainerApp(
                location=self._location,
                tags={"genie-managed-by": "genie", "genie-mission-id": mission_slug},
                managed_environment_id=self._container_apps_environment_id,
                identity=ManagedServiceIdentity(
                    type="UserAssigned",
                    user_assigned_identities={
                        mission_identity_resource_id: UserAssignedIdentity()
                    },
                ),
                configuration=Configuration(
                    ingress=Ingress(external=True, target_port=80),
                    registries=[
                        RegistryCredentials(
                            server=f"{self._acr_name}.azurecr.io",
                            identity=mission_identity_resource_id,
                        )
                    ],
                    secrets=[],
                ),
                template=Template(
                    containers=[Container(name="frontend", image=image_tag)],
                    scale=Scale(min_replicas=1, max_replicas=10),
                ),
            )
            await report("Creating/updating the mission frontend Container App...")
            result = self._container_apps_client().container_apps.begin_create_or_update(
                prototype_resource_group_name(mission_slug), app_name, envelope
            ).result()
        except Exception as exc:
            raise ContainerAppFrontendDeploymentError(
                f"Failed to deploy frontend Container App: {exc}"
            ) from exc

        fqdn = getattr(getattr(result.configuration, "ingress", None), "fqdn", None)
        if not fqdn:
            raise ContainerAppFrontendDeploymentError(
                f"Frontend Container App '{app_name}' returned no ingress hostname."
            )
        return ContainerAppFrontendDeploymentResult(
            frontend_url=f"https://{fqdn}", image_tag=image_tag
        )

    async def delete(self, *, mission_slug: str, app_name: str | None = None) -> None:
        """Deletes the generated frontend Container App for an abandoned run."""

        app_name = app_name or f"genie-{mission_slug}-frontend"
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
                raise ContainerAppFrontendDeploymentError(
                    f"Failed to delete frontend Container App '{app_name}': {exc}"
                ) from exc


class NullContainerAppFrontendDeploymentService:
    async def deploy(
        self,
        *,
        mission_slug: str,
        ui_root: Path,
        app_name: str | None = None,
        mission_identity_resource_id: str | None = None,
        on_progress: DeploymentProgressCallback | None = None,
    ) -> ContainerAppFrontendDeploymentResult:
        del ui_root, app_name, mission_identity_resource_id
        if on_progress is not None:
            await on_progress("Deploying frontend Container App (local mode)...")
        return ContainerAppFrontendDeploymentResult(
            frontend_url=f"http://localhost/missions/{mission_slug}/frontend",
            image_tag=f"local/{mission_slug}-frontend:dev",
        )

    async def delete(self, *, mission_slug: str, app_name: str | None = None) -> None:
        del mission_slug, app_name


def create_container_app_frontend_deployment_service(
    *, settings: Settings
) -> ContainerAppFrontendDeploymentService | NullContainerAppFrontendDeploymentService:
    required = (
        settings.azure_subscription_id,
        settings.deployment_resource_group,
        settings.deployment_acr_name,
        settings.deployment_container_apps_environment_id,
        settings.deployment_location,
    )
    if not all(required):
        raise ContainerAppFrontendDeploymentError(
            "azure_subscription_id, deployment_resource_group, deployment_acr_name, "
            "deployment_container_apps_environment_id, and deployment_location must "
            "all be configured; local/fake frontend deployment is not permitted."
        )
    return ContainerAppFrontendDeploymentService(
        subscription_id=settings.azure_subscription_id,  # type: ignore[arg-type]
        resource_group=settings.deployment_resource_group,  # type: ignore[arg-type]
        acr_name=settings.deployment_acr_name,  # type: ignore[arg-type]
        container_apps_environment_id=settings.deployment_container_apps_environment_id,  # type: ignore[arg-type]
        location=settings.deployment_location,  # type: ignore[arg-type]
    )
