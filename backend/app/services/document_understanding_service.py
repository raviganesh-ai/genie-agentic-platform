"""Customer-evidence extraction through Azure Content Understanding."""
from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx
from azure.core.credentials_async import AsyncTokenCredential

from app.config.settings import Settings
from app.utils.document_text import DocumentTextExtractionError, extract_text

__all__ = [
    "AzureContentUnderstandingService",
    "DocumentUnderstandingError",
    "DocumentUnderstandingService",
    "LocalDocumentUnderstandingService",
    "create_document_understanding_service",
]

_COGNITIVE_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"
_IMAGE_EXTENSIONS = {".bmp", ".heic", ".heif", ".jpe", ".jpeg", ".jpg", ".png"}
_OPEN_XML_EXTENSIONS = {".docm", ".docx", ".pptm", ".pptx", ".xlsm", ".xlsx"}
_OLE_COMPOUND_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
_ENCRYPTED_PACKAGE_MARKER = "EncryptedPackage".encode("utf-16-le")
_SUPPORTED_EXTENSIONS = _IMAGE_EXTENSIONS | {
    ".csv",
    ".doc",
    ".docm",
    ".docx",
    ".eml",
    ".epub",
    ".html",
    ".json",
    ".kml",
    ".md",
    ".msg",
    ".odp",
    ".ods",
    ".odt",
    ".pdf",
    ".ppt",
    ".pptm",
    ".pptx",
    ".rtf",
    ".tif",
    ".tiff",
    ".tsv",
    ".txt",
    ".xls",
    ".xlsm",
    ".xlsx",
    ".xml",
}


class DocumentUnderstandingError(RuntimeError):
    """Raised when customer evidence cannot be understood safely."""


class DocumentUnderstandingService(Protocol):
    """Extract grounded Markdown from one uploaded evidence file."""

    async def validate_ready(self) -> None: ...

    async def extract(
        self, *, content: bytes, content_type: str, file_name: str
    ) -> str: ...


def _reject_protected_open_xml(*, content: bytes, file_name: str) -> None:
    extension = Path(file_name).suffix.lower()
    if (
        extension in _OPEN_XML_EXTENSIONS
        and content.startswith(_OLE_COMPOUND_MAGIC)
        and _ENCRYPTED_PACKAGE_MARKER in content
    ):
        raise DocumentUnderstandingError(
            f"Office document '{file_name}' is encrypted or rights-protected and cannot be "
            "analyzed. Open it in Microsoft Office with authorized access and, if policy "
            "permits, save an unprotected PDF or Office copy before uploading it again."
        )


class LocalDocumentUnderstandingService:
    """Deterministic extraction for local development and tests only."""

    async def validate_ready(self) -> None:
        return None

    async def extract(self, *, content: bytes, content_type: str, file_name: str) -> str:
        _reject_protected_open_xml(content=content, file_name=file_name)
        try:
            return extract_text(content=content, content_type=content_type, file_name=file_name)
        except DocumentTextExtractionError as exc:
            raise DocumentUnderstandingError(str(exc)) from exc


class AzureContentUnderstandingService:
    """Analyze document and image evidence with Content Understanding GA."""

    def __init__(
        self,
        *,
        endpoint: str,
        analyzer_id: str,
        api_version: str,
        processing_location: str,
        timeout_seconds: float,
        poll_interval_seconds: float,
        credential: AsyncTokenCredential | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not endpoint.strip():
            raise DocumentUnderstandingError("Content Understanding endpoint must not be blank.")
        parsed_endpoint = urlsplit(endpoint)
        if (
            parsed_endpoint.scheme != "https"
            or not parsed_endpoint.hostname
            or parsed_endpoint.username
            or parsed_endpoint.password
            or parsed_endpoint.query
            or parsed_endpoint.fragment
            or parsed_endpoint.path.rstrip("/")
        ):
            raise DocumentUnderstandingError(
                "Content Understanding endpoint must be an HTTPS account root URL."
            )
        self._endpoint = endpoint.rstrip("/")
        self._analyzer_id = analyzer_id
        self._api_version = api_version
        self._processing_location = processing_location
        self._timeout_seconds = timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._credential = credential
        self._http_client = http_client

    async def validate_ready(self) -> None:
        credential = self._credential
        owns_credential = credential is None
        if credential is None:
            from azure.identity.aio import DefaultAzureCredential

            credential = DefaultAzureCredential()

        client = self._http_client
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=self._timeout_seconds)

        try:
            token = await credential.get_token(_COGNITIVE_SERVICES_SCOPE)
            headers = {"Authorization": f"Bearer {token.token}"}
            analyzer_response = await client.get(
                (
                    f"{self._endpoint}/contentunderstanding/analyzers/"
                    f"{self._analyzer_id}"
                ),
                params={"api-version": self._api_version},
                headers=headers,
            )
            analyzer_response.raise_for_status()
            analyzer = analyzer_response.json()
            if analyzer.get("status") != "ready":
                raise DocumentUnderstandingError(
                    f"Content Understanding analyzer '{self._analyzer_id}' is not ready."
                )

            defaults_response = await client.get(
                f"{self._endpoint}/contentunderstanding/defaults",
                params={"api-version": self._api_version},
                headers=headers,
            )
            defaults_response.raise_for_status()
            model_deployments = defaults_response.json().get("modelDeployments") or {}
            required_aliases = {
                value
                for value in (analyzer.get("models") or {}).values()
                if isinstance(value, str) and value
            }
            if not required_aliases:
                raise DocumentUnderstandingError(
                    f"Content Understanding analyzer '{self._analyzer_id}' reported no model aliases."
                )
            missing_aliases = sorted(
                alias for alias in required_aliases if not model_deployments.get(alias)
            )
            if missing_aliases:
                raise DocumentUnderstandingError(
                    "Content Understanding model defaults are missing for: "
                    + ", ".join(missing_aliases)
                )
        except DocumentUnderstandingError:
            raise
        except httpx.HTTPError as exc:
            raise DocumentUnderstandingError(
                f"Content Understanding readiness validation failed: {exc}"
            ) from exc
        except Exception as exc:
            raise DocumentUnderstandingError(
                f"Content Understanding readiness validation failed: {exc}"
            ) from exc
        finally:
            if owns_client:
                await client.aclose()
            if owns_credential:
                await credential.close()

    async def extract(self, *, content: bytes, content_type: str, file_name: str) -> str:
        extension = Path(file_name).suffix.lower()
        if extension not in _SUPPORTED_EXTENSIONS:
            raise DocumentUnderstandingError(
                f"Unsupported evidence file type '{extension or 'unknown'}' for '{file_name}'."
            )
        if not content:
            raise DocumentUnderstandingError(f"Evidence file '{file_name}' is empty.")
        _reject_protected_open_xml(content=content, file_name=file_name)

        credential = self._credential
        owns_credential = credential is None
        if credential is None:
            from azure.identity.aio import DefaultAzureCredential

            credential = DefaultAzureCredential()

        client = self._http_client
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=self._timeout_seconds)

        try:
            token = await credential.get_token(_COGNITIVE_SERVICES_SCOPE)
            headers = {
                "Authorization": f"Bearer {token.token}",
                "Content-Type": content_type or "application/octet-stream",
                "x-ms-client-request-id": uuid.uuid4().hex,
            }
            analyze_url = (
                f"{self._endpoint}/contentunderstanding/analyzers/"
                f"{self._analyzer_id}:analyzeBinary"
            )
            response = await client.post(
                analyze_url,
                params={
                    "api-version": self._api_version,
                    "processingLocation": self._processing_location,
                },
                headers=headers,
                content=content,
            )
            response.raise_for_status()
            operation_location = response.headers.get("Operation-Location")
            if not operation_location:
                raise DocumentUnderstandingError(
                    "Content Understanding accepted the file without an operation location."
                )
            self._validate_operation_location(operation_location)
            payload = await self._poll(client, operation_location, headers)
            return self._extract_markdown(payload, file_name=file_name)
        except DocumentUnderstandingError:
            raise
        except httpx.HTTPError as exc:
            raise DocumentUnderstandingError(
                f"Azure Content Understanding request failed for '{file_name}': {exc}"
            ) from exc
        except Exception as exc:
            raise DocumentUnderstandingError(
                f"Azure Content Understanding could not analyze '{file_name}': {exc}"
            ) from exc
        finally:
            if owns_client:
                await client.aclose()
            if owns_credential:
                await credential.close()

    def _validate_operation_location(self, operation_location: str) -> None:
        expected = urlsplit(self._endpoint)
        actual = urlsplit(operation_location)
        trusted_hosts = {expected.netloc.lower()}
        for suffix in (".services.ai.azure.com", ".cognitiveservices.azure.com"):
            if expected.netloc.lower().endswith(suffix):
                account_name = expected.netloc[: -len(suffix)]
                trusted_hosts.update(
                    {
                        f"{account_name}.services.ai.azure.com".lower(),
                        f"{account_name}.cognitiveservices.azure.com".lower(),
                    }
                )
                break
        if actual.scheme != "https" or actual.netloc.lower() not in trusted_hosts:
            raise DocumentUnderstandingError(
                "Content Understanding returned an invalid operation location."
            )

    async def _poll(
        self,
        client: httpx.AsyncClient,
        operation_location: str,
        request_headers: dict[str, str],
    ) -> dict[str, Any]:
        deadline = time.monotonic() + self._timeout_seconds
        headers = {"Authorization": request_headers["Authorization"]}
        while time.monotonic() < deadline:
            response = await client.get(operation_location, headers=headers)
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
            state = str(payload.get("status", ""))
            if state == "Succeeded":
                return payload
            if state in {"Failed", "Canceled"}:
                error = payload.get("error") or {}
                message = error.get("message") or "analysis did not complete"
                raise DocumentUnderstandingError(f"Content Understanding {state.lower()}: {message}")
            await asyncio.sleep(self._poll_interval_seconds)
        raise DocumentUnderstandingError(
            f"Content Understanding did not finish within {self._timeout_seconds:g} seconds."
        )

    @staticmethod
    def _extract_markdown(payload: dict[str, Any], *, file_name: str) -> str:
        contents = ((payload.get("result") or {}).get("contents") or [])
        sections: list[str] = []
        for item in contents:
            markdown = str(item.get("markdown") or "").strip()
            if markdown:
                sections.append(markdown)
            summary = ((item.get("fields") or {}).get("Summary") or {}).get("valueString")
            if summary:
                sections.append(f"## Generated evidence summary\n\n{summary}")
            for figure in item.get("figures") or []:
                description = str(figure.get("description") or "").strip()
                representation = str(figure.get("content") or "").strip()
                if description or representation:
                    sections.append(
                        "## Generated visual analysis\n\n"
                        + "\n\n".join(part for part in (description, representation) if part)
                    )
        extracted = "\n\n".join(sections).strip()
        if not extracted:
            raise DocumentUnderstandingError(
                f"Content Understanding returned no usable evidence for '{file_name}'."
            )
        return extracted


def _foundry_account_endpoint(project_endpoint: str | None) -> str | None:
    if not project_endpoint:
        return None
    parsed = urlsplit(project_endpoint)
    if parsed.scheme != "https" or not parsed.netloc:
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")


def create_document_understanding_service(settings: Settings) -> DocumentUnderstandingService:
    if settings.allow_local_agents:
        return LocalDocumentUnderstandingService()

    endpoint = settings.azure_content_understanding_endpoint or _foundry_account_endpoint(
        settings.azure_foundry_endpoint
    )
    if not endpoint:
        raise DocumentUnderstandingError(
            "No Content Understanding endpoint is configured for production evidence ingestion."
        )
    return AzureContentUnderstandingService(
        endpoint=endpoint,
        analyzer_id=settings.content_understanding_analyzer_id,
        api_version=settings.content_understanding_api_version,
        processing_location=settings.content_understanding_processing_location,
        timeout_seconds=settings.content_understanding_timeout_seconds,
        poll_interval_seconds=settings.content_understanding_poll_interval_seconds,
    )