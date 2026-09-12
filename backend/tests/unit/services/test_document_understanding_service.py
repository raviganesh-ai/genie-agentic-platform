"""Wire-contract tests for Azure Content Understanding evidence extraction."""
from __future__ import annotations

from typing import Any

import httpx
import pytest
from azure.core.credentials import AccessToken

from app.config.settings import Settings
from app.services.document_understanding_service import (
    AzureContentUnderstandingService,
    DocumentUnderstandingError,
    LocalDocumentUnderstandingService,
    create_document_understanding_service,
)


class FakeCredential:
    def __init__(self) -> None:
        self.scopes: list[str] = []
        self.closed = False

    async def get_token(self, *scopes: str, **_: Any) -> AccessToken:
        self.scopes.extend(scopes)
        return AccessToken("content-understanding-token", 4_102_444_800)

    async def close(self) -> None:
        self.closed = True


def _service(
    handler: httpx.AsyncBaseTransport,
    *,
    endpoint: str = "https://genie.services.ai.azure.com",
) -> AzureContentUnderstandingService:
    return AzureContentUnderstandingService(
        endpoint=endpoint,
        analyzer_id="prebuilt-documentSearch",
        api_version="2025-11-01",
        processing_location="geography",
        timeout_seconds=5,
        poll_interval_seconds=0.1,
        credential=FakeCredential(),  # type: ignore[arg-type]
        http_client=httpx.AsyncClient(transport=handler),
    )


async def test_analyze_binary_polls_and_returns_grounded_markdown() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(
                202,
                headers={
                    "Operation-Location": (
                        "https://genie.services.ai.azure.com/contentunderstanding/"
                        "analyzerResults/result-1?api-version=2025-11-01"
                    )
                },
            )
        return httpx.Response(
            200,
            json={
                "status": "Succeeded",
                "result": {
                    "contents": [
                        {
                            "markdown": "# Customer process\n\nA manual approval blocks fulfillment.",
                            "fields": {
                                "Summary": {"valueString": "The process has a manual bottleneck."}
                            },
                            "figures": [
                                {
                                    "description": "A flow diagram with one approval queue.",
                                    "content": "flowchart LR\nA --> B",
                                }
                            ],
                        }
                    ]
                },
            },
        )

    credential = FakeCredential()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = AzureContentUnderstandingService(
        endpoint="https://genie.services.ai.azure.com",
        analyzer_id="prebuilt-documentSearch",
        api_version="2025-11-01",
        processing_location="geography",
        timeout_seconds=5,
        poll_interval_seconds=0.1,
        credential=credential,  # type: ignore[arg-type]
        http_client=client,
    )

    result = await service.extract(
        content=b"png bytes",
        content_type="image/png",
        file_name="current-state.png",
    )
    await client.aclose()

    assert credential.scopes == ["https://cognitiveservices.azure.com/.default"]
    assert len(requests) == 2
    analyze_request = requests[0]
    assert analyze_request.method == "POST"
    assert analyze_request.url.path.endswith(
        "/contentunderstanding/analyzers/prebuilt-documentSearch:analyzeBinary"
    )
    assert analyze_request.url.params["api-version"] == "2025-11-01"
    assert analyze_request.url.params["processingLocation"] == "geography"
    assert analyze_request.headers["authorization"] == "Bearer content-understanding-token"
    assert analyze_request.headers["content-type"] == "image/png"
    assert analyze_request.content == b"png bytes"
    assert "manual approval blocks fulfillment" in result
    assert "Generated evidence summary" in result
    assert "manual bottleneck" in result
    assert "Generated visual analysis" in result
    assert "flowchart LR" in result


async def test_readiness_requires_every_analyzer_model_alias_to_have_a_default() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/prebuilt-documentSearch"):
            return httpx.Response(
                200,
                json={
                    "status": "ready",
                    "models": {
                        "completion": "prebuilt-analyzer-completion-mini",
                        "embedding": "prebuilt-analyzer-embedding",
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "modelDeployments": {
                    "prebuilt-analyzer-completion-mini": "gpt-5-mini",
                }
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = AzureContentUnderstandingService(
        endpoint="https://genie.services.ai.azure.com",
        analyzer_id="prebuilt-documentSearch",
        api_version="2025-11-01",
        processing_location="geography",
        timeout_seconds=5,
        poll_interval_seconds=0.1,
        credential=FakeCredential(),  # type: ignore[arg-type]
        http_client=client,
    )

    with pytest.raises(DocumentUnderstandingError, match="prebuilt-analyzer-embedding"):
        await service.validate_ready()
    await client.aclose()


async def test_analysis_failure_surfaces_azure_message() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                202,
                headers={"Operation-Location": "https://genie.services.ai.azure.com/result/1"},
            )
        return httpx.Response(
            200,
            json={"status": "Failed", "error": {"message": "The file is encrypted."}},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = _service(client._transport)
    with pytest.raises(DocumentUnderstandingError, match="The file is encrypted"):
        await service.extract(content=b"pdf", content_type="application/pdf", file_name="x.pdf")
    await service._http_client.aclose()  # type: ignore[union-attr]


async def test_cross_host_operation_location_is_rejected() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            202,
            headers={"Operation-Location": "https://attacker.example/result/1"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = _service(client._transport)
    with pytest.raises(DocumentUnderstandingError, match="invalid operation location"):
        await service.extract(content=b"pdf", content_type="application/pdf", file_name="x.pdf")
    await service._http_client.aclose()  # type: ignore[union-attr]


async def test_empty_success_result_is_rejected() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                202,
                headers={"Operation-Location": "https://genie.services.ai.azure.com/result/1"},
            )
        return httpx.Response(200, json={"status": "Succeeded", "result": {"contents": []}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = _service(client._transport)
    with pytest.raises(DocumentUnderstandingError, match="no usable evidence"):
        await service.extract(content=b"pdf", content_type="application/pdf", file_name="x.pdf")
    await service._http_client.aclose()  # type: ignore[union-attr]


async def test_unsupported_binary_type_is_rejected_before_azure_call() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        pytest.fail("Unsupported input must not be sent to Azure")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = _service(client._transport)
    with pytest.raises(DocumentUnderstandingError, match="Unsupported evidence file type"):
        await service.extract(
            content=b"archive", content_type="application/zip", file_name="evidence.zip"
        )
    await service._http_client.aclose()  # type: ignore[union-attr]


async def test_protected_docx_is_rejected_with_actionable_message_before_azure_call() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        pytest.fail("Protected Office content must not be sent to Azure")

    protected_docx = (
        bytes.fromhex("d0cf11e0a1b11ae1")
        + b"\x00" * 64
        + "EncryptedPackage".encode("utf-16-le")
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = _service(client._transport)

    with pytest.raises(
        DocumentUnderstandingError,
        match="encrypted or rights-protected.*save an unprotected PDF or Office copy",
    ):
        await service.extract(
            content=protected_docx,
            content_type=(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
            file_name="protected.docx",
        )
    await service._http_client.aclose()  # type: ignore[union-attr]


async def test_local_service_rejects_protected_docx_with_same_actionable_message() -> None:
    protected_docx = (
        bytes.fromhex("d0cf11e0a1b11ae1")
        + b"\x00" * 64
        + "EncryptedPackage".encode("utf-16-le")
    )

    with pytest.raises(DocumentUnderstandingError, match="encrypted or rights-protected"):
        await LocalDocumentUnderstandingService().extract(
            content=protected_docx,
            content_type=(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
            file_name="protected.docx",
        )


def test_factory_uses_local_extraction_only_when_explicitly_allowed() -> None:
    local = create_document_understanding_service(Settings(allow_local_agents=True))
    production = create_document_understanding_service(
        Settings(
            allow_local_agents=False,
            azure_foundry_endpoint=(
                "https://genie.services.ai.azure.com/api/projects/genie-project"
            ),
        )
    )

    assert isinstance(local, LocalDocumentUnderstandingService)
    assert isinstance(production, AzureContentUnderstandingService)


def test_service_rejects_non_root_or_insecure_endpoint() -> None:
    for endpoint in (
        "http://genie.services.ai.azure.com",
        "https://genie.services.ai.azure.com/api/projects/project",
    ):
        with pytest.raises(DocumentUnderstandingError, match="HTTPS account root URL"):
            AzureContentUnderstandingService(
                endpoint=endpoint,
                analyzer_id="prebuilt-documentSearch",
                api_version="2025-11-01",
                processing_location="geography",
                timeout_seconds=5,
                poll_interval_seconds=0.1,
            )