from __future__ import annotations

import httpx
import pytest

from app.config.settings import Settings
from app.security.token_validator import (
    AuthenticationError,
    AuthenticationServiceUnavailableError,
    MiseTokenValidator,
    TokenValidatorError,
    create_token_validator,
)


@pytest.mark.asyncio
async def test_mise_forwards_original_request_context_and_returns_claims() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/ValidateRequest"
        assert request.headers["authorization"] == "Bearer token-value"
        assert request.headers["original-method"] == "PATCH"
        assert request.headers["original-uri"] == "/api/sessions/123"
        assert request.headers["return-subject-token-claim-oid"] == "1"
        assert request.headers["return-subject-token-claim-roles"] == "1"
        return httpx.Response(
            200,
            headers={
                "Subject-Token-Claim-oid": "user-1",
                "Subject-Token-Claim-name": "Test User",
                "Subject-Token-Claim-roles": '["foundry-admin"]',
            },
        )

    validator = MiseTokenValidator(
        endpoint="http://mise-sidecar:8080",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )
    try:
        claims = await validator.validate(
            "token-value",
            method="PATCH",
            path="/api/sessions/123",
        )
    finally:
        await validator.close()

    assert claims["oid"] == "user-1"
    assert claims["roles"] == ["foundry-admin"]


@pytest.mark.asyncio
async def test_mise_preserves_authentication_rejection_without_response_body() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            text="sensitive upstream detail",
        )

    validator = MiseTokenValidator(
        endpoint="http://mise-sidecar:8080",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(AuthenticationError) as exc_info:
            await validator.validate("bad-token", method="GET", path="/api/sessions")
    finally:
        await validator.close()

    assert exc_info.value.status_code == 401
    assert exc_info.value.www_authenticate == 'Bearer error="invalid_token"'
    assert "sensitive" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_mise_fails_closed_when_sidecar_is_unavailable() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    validator = MiseTokenValidator(
        endpoint="http://mise-sidecar:8080",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(AuthenticationServiceUnavailableError):
            await validator.validate("token", method="GET", path="/api/sessions")
    finally:
        await validator.close()


@pytest.mark.asyncio
async def test_mise_fails_closed_when_success_response_has_no_claims() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    validator = MiseTokenValidator(
        endpoint="http://mise-sidecar:8080",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(AuthenticationError, match="subject claim"):
            await validator.validate("token", method="GET", path="/api/sessions")
    finally:
        await validator.close()


def test_production_factory_requires_mise() -> None:
    settings = Settings(
        provider_mode="production",
        allow_local_agents=False,
        entra_tenant_id="tenant-id",
        entra_client_id="client-id",
    )

    with pytest.raises(TokenValidatorError, match="mise_endpoint"):
        create_token_validator(settings)


@pytest.mark.asyncio
async def test_factory_uses_mise_when_configured() -> None:
    settings = Settings(
        provider_mode="production",
        allow_local_agents=False,
        entra_tenant_id="tenant-id",
        entra_client_id="client-id",
        mise_endpoint="http://mise-sidecar:8080",
    )

    validator = create_token_validator(settings)

    assert isinstance(validator, MiseTokenValidator)
    await validator.close()
