"""Unit tests for the centralized domain-error -> HTTP status mapping."""
from __future__ import annotations

import logging

import pytest
from fastapi import Request, status

from app.agents.foundry.errors import FoundryAgentSynchronizationError, FoundryUnavailableError
from app.api.error_mapping import _status_code_for, domain_error_handler
from app.services.customer_agent_provisioning_service import CustomerAgentProvisioningError
from app.services.session_service import SessionNotFoundError


@pytest.mark.parametrize(
    ("exc", "expected_status"),
    [
        (FoundryUnavailableError("no output text"), status.HTTP_503_SERVICE_UNAVAILABLE),
        (
            FoundryAgentSynchronizationError("sync failed"),
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ),
        (
            CustomerAgentProvisioningError("provisioning failed"),
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ),
        (SessionNotFoundError("missing"), status.HTTP_404_NOT_FOUND),
        (RuntimeError("unmapped"), status.HTTP_400_BAD_REQUEST),
    ],
)
def test_status_code_for_maps_known_domain_errors(
    exc: Exception, expected_status: int
) -> None:
    assert _status_code_for(exc) == expected_status


def _make_request(path: str = "/sessions/abc/workflows/x/run") -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [(b"x-correlation-id", b"corr-123")],
        "query_string": b"",
    }
    return Request(scope)


async def test_domain_error_handler_returns_503_and_logs_for_foundry_unavailable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    exc = FoundryUnavailableError(
        "Azure AI Foundry run produced no output text for agent 'genie-orchestrator' (version '2')."
    )
    with caplog.at_level(logging.ERROR, logger="app.api.error_mapping"):
        response = await domain_error_handler(_make_request(), exc)

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert any("corr-123" in record.message for record in caplog.records)
    assert any("FoundryUnavailableError" in record.message for record in caplog.records)
