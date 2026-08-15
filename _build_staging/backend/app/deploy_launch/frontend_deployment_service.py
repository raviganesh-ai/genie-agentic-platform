"""Real frontend deployment: Azure Storage static website hosting.

Production path (selected the same way as ``BackendDeploymentService`` -
see ``create_frontend_deployment_service`` below): uploads the mission's
materialized UI build (see ``app.deploy_launch.code_materializer``) to the
storage account's ``$web`` container and enables static website hosting -
simpler and more reliable than the Static Web Apps deploy-token API, and
sufficient for a single-page mission UI with no server-side rendering.

Uses ``DefaultAzureCredential`` against the *data-plane*
``azure.storage.blob.BlobServiceClient`` for both uploading blobs and
enabling static website hosting (``set_service_properties(static_website=
...)``) - never a hardcoded account key, per the Security Requirements in
``.github/copilot-instructions.md``. Assumption documented per the Coding
Instructions: Azure Storage's management-plane SDK
(``azure-mgmt-storage``) does not expose static website configuration at
all in the installed API version - that is a real, intentional Azure
platform behavior (static website hosting is a data-plane-only setting),
not something invented here.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config.settings import Settings

# Invoked with a short human-readable message right before each real
# sub-phase of ``deploy()`` (enabling static website hosting, uploading the
# build) - mirrors ``backend_deployment_service.DeploymentProgressCallback``.
DeploymentProgressCallback = Callable[[str], Awaitable[None]]

__all__ = [
    "FrontendDeploymentError",
    "FrontendDeploymentResult",
    "FrontendDeploymentService",
    "NullFrontendDeploymentService",
    "create_frontend_deployment_service",
]

_WEB_CONTAINER = "$web"


class FrontendDeploymentError(RuntimeError):
    """Raised when deploying the mission's frontend static site fails."""


@dataclass(frozen=True)
class FrontendDeploymentResult:
    frontend_url: str


def _content_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".html": "text/html",
        ".js": "application/javascript",
        ".css": "text/css",
        ".json": "application/json",
        ".svg": "image/svg+xml",
        ".png": "image/png",
    }.get(suffix, "application/octet-stream")


class FrontendDeploymentService:
    """Uploads a mission's UI build to Azure Storage static website hosting."""

    def __init__(self, *, subscription_id: str, resource_group: str, storage_account_name: str) -> None:
        self._subscription_id = subscription_id
        self._resource_group = resource_group
        self._storage_account_name = storage_account_name

    def _blob_service_client(self) -> Any:
        try:
            from azure.identity import DefaultAzureCredential
            from azure.storage.blob import BlobServiceClient
        except ImportError as exc:
            raise FrontendDeploymentError(
                "azure-storage-blob / azure-identity are not installed."
            ) from exc
        try:
            return BlobServiceClient(
                account_url=f"https://{self._storage_account_name}.blob.core.windows.net",
                credential=DefaultAzureCredential(),
            )
        except Exception as exc:
            raise FrontendDeploymentError(f"Failed to construct Blob service client: {exc}") from exc

    def _storage_mgmt_client(self) -> Any:
        try:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.storage import StorageManagementClient
        except ImportError as exc:
            raise FrontendDeploymentError(
                "azure-mgmt-storage / azure-identity are not installed."
            ) from exc
        try:
            return StorageManagementClient(DefaultAzureCredential(), self._subscription_id)
        except Exception as exc:
            raise FrontendDeploymentError(f"Failed to construct Storage management client: {exc}") from exc

    async def deploy(
        self, *, ui_root: Path, on_progress: DeploymentProgressCallback | None = None
    ) -> FrontendDeploymentResult:
        """Enables static website hosting and uploads every file under ``ui_root``.

        When given, ``on_progress`` is awaited with a short status message
        before each real sub-phase begins.
        """

        async def _report(message: str) -> None:
            if on_progress is not None:
                await on_progress(message)

        if not ui_root.exists() or not any(ui_root.iterdir()):
            raise FrontendDeploymentError(f"No materialized UI build found at '{ui_root}'.")

        try:
            from azure.storage.blob import ContentSettings, StaticWebsite

            await _report("Enabling static website hosting on Azure Storage...")
            blob_service_client = self._blob_service_client()
            blob_service_client.set_service_properties(
                static_website=StaticWebsite(
                    enabled=True,
                    index_document="index.html",
                    error_document404_path="index.html",
                )
            )

            container_client = blob_service_client.get_container_client(_WEB_CONTAINER)
            build_files = [path for path in sorted(ui_root.rglob("*")) if path.is_file()]
            await _report(f"Uploading {len(build_files)} frontend file(s) to Azure Storage...")
            for file_path in build_files:
                blob_name = file_path.relative_to(ui_root).as_posix()
                with file_path.open("rb") as handle:
                    container_client.upload_blob(
                        name=blob_name,
                        data=handle,
                        overwrite=True,
                        content_settings=ContentSettings(content_type=_content_type_for(file_path)),
                    )
        except FrontendDeploymentError:
            raise
        except Exception as exc:
            raise FrontendDeploymentError(f"Failed to upload frontend build: {exc}") from exc

        try:
            await _report("Reading the static website endpoint...")
            storage_client = self._storage_mgmt_client()
            account = storage_client.storage_accounts.get_properties(
                self._resource_group, self._storage_account_name
            )
            frontend_url = account.primary_endpoints.web
        except Exception as exc:
            raise FrontendDeploymentError(f"Failed to read static website endpoint: {exc}") from exc

        return FrontendDeploymentResult(frontend_url=frontend_url or "")


class NullFrontendDeploymentService:
    """Local/test double: real behavior end-to-end minus any actual Azure calls."""

    async def deploy(
        self, *, ui_root: Path, on_progress: DeploymentProgressCallback | None = None
    ) -> FrontendDeploymentResult:
        del ui_root
        if on_progress is not None:
            await on_progress("Deploying frontend (local mode, no real Azure calls)...")
        return FrontendDeploymentResult(frontend_url="http://localhost/missions/frontend")


def create_frontend_deployment_service(
    *, settings: Settings
) -> FrontendDeploymentService | NullFrontendDeploymentService:
    """Fail-closed factory mirroring ``create_backend_deployment_service``."""

    required = (
        settings.azure_subscription_id,
        settings.deployment_resource_group,
        settings.deployment_storage_account_name,
    )

    def _build_real() -> FrontendDeploymentService:
        if not all(required):
            raise FrontendDeploymentError(
                "azure_subscription_id, deployment_resource_group, and "
                "deployment_storage_account_name must all be configured to "
                "deploy a mission's frontend."
            )
        return FrontendDeploymentService(
            subscription_id=settings.azure_subscription_id,  # type: ignore[arg-type]
            resource_group=settings.deployment_resource_group,  # type: ignore[arg-type]
            storage_account_name=settings.deployment_storage_account_name,  # type: ignore[arg-type]
        )

    if all(required):
        return _build_real()

    return NullFrontendDeploymentService()
