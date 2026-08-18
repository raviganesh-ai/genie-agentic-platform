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

DeploymentProgressCallback = Callable[[str], Awaitable[None]]
_ACR_RUN_FAILURE_STATUSES = frozenset({"failed", "canceled", "cancelled", "error", "timeout"})

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

    def _acr_credentials_client(self) -> Any:
        try:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.containerregistry import ContainerRegistryManagementClient
            return ContainerRegistryManagementClient(DefaultAzureCredential(), self._subscription_id)
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
        on_progress: DeploymentProgressCallback | None = None,
    ) -> ContainerAppFrontendDeploymentResult:
        async def report(message: str) -> None:
            if on_progress is not None:
                await on_progress(message)

        if not ui_root.exists() or not any(ui_root.iterdir()):
            raise ContainerAppFrontendDeploymentError(
                f"No materialized UI build found at '{ui_root}'."
            )

        dockerfile = ui_root / "Dockerfile"
        dockerfile.write_text(
            "FROM node:20-alpine AS build\n"
            "WORKDIR /app\n"
            "COPY package.json ./\n"
            "RUN npm install\n"
            "COPY . .\n"
            "RUN npm run build\n"
            "FROM nginx:alpine\n"
            "COPY --from=build /app/dist /usr/share/nginx/html\n"
            "EXPOSE 80\n",
            encoding="utf-8",
        )
        app_name = f"genie-{mission_slug}-frontend"
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
            await report("Reading Azure Container Registry credentials...")
            credentials = self._acr_credentials_client().registries.list_credentials(
                self._resource_group, self._acr_name
            )
            username = credentials.username
            password = credentials.passwords[0].value

            from azure.mgmt.appcontainers.models import (
                Configuration,
                Container,
                ContainerApp,
                Ingress,
                RegistryCredentials,
                Scale,
                Secret,
                Template,
            )

            envelope = ContainerApp(
                location=self._location,
                managed_environment_id=self._container_apps_environment_id,
                configuration=Configuration(
                    ingress=Ingress(external=True, target_port=80),
                    registries=[
                        RegistryCredentials(
                            server=f"{self._acr_name}.azurecr.io",
                            username=username,
                            password_secret_ref="acr-password",
                        )
                    ],
                    secrets=[Secret(name="acr-password", value=password)],
                ),
                template=Template(
                    containers=[Container(name="frontend", image=image_tag)],
                    scale=Scale(min_replicas=1, max_replicas=10),
                ),
            )
            await report("Creating/updating the mission frontend Container App...")
            result = self._container_apps_client().container_apps.begin_create_or_update(
                self._resource_group, app_name, envelope
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


class NullContainerAppFrontendDeploymentService:
    async def deploy(
        self,
        *,
        mission_slug: str,
        ui_root: Path,
        on_progress: DeploymentProgressCallback | None = None,
    ) -> ContainerAppFrontendDeploymentResult:
        del ui_root
        if on_progress is not None:
            await on_progress("Deploying frontend Container App (local mode)...")
        return ContainerAppFrontendDeploymentResult(
            frontend_url=f"http://localhost/missions/{mission_slug}/frontend",
            image_tag=f"local/{mission_slug}-frontend:dev",
        )


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
