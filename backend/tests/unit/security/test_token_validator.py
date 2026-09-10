from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config.settings import Settings
from app.security.token_validator import (
    AuthenticationError,
    EntraTokenValidator,
    LocalDevTokenValidator,
    TokenValidatorError,
    create_token_validator,
)


@pytest.mark.asyncio
async def test_local_dev_validator_decodes_claims_without_verifying_signature() -> None:
    import jwt

    token = jwt.encode({"oid": "user-1", "name": "Test User"}, "dummy-secret", algorithm="HS256")

    validator = LocalDevTokenValidator()
    claims = await validator.validate(token, method="GET", path="/api/sessions")

    assert claims["oid"] == "user-1"
    assert claims["name"] == "Test User"


@pytest.mark.asyncio
async def test_local_dev_validator_rejects_malformed_token() -> None:
    validator = LocalDevTokenValidator()

    with pytest.raises(AuthenticationError, match="Malformed bearer token"):
        await validator.validate("not-a-jwt", method="GET", path="/api/sessions")


@pytest.mark.asyncio
async def test_local_dev_validator_rejects_token_without_subject_claim() -> None:
    import jwt

    token = jwt.encode({"name": "Test User"}, "dummy-secret", algorithm="HS256")
    validator = LocalDevTokenValidator()

    with pytest.raises(AuthenticationError, match="sub' or 'oid'"):
        await validator.validate(token, method="GET", path="/api/sessions")


def test_factory_uses_local_dev_validator_when_allowed() -> None:
    settings = Settings(allow_local_token_validation=True)

    validator = create_token_validator(settings)

    assert isinstance(validator, LocalDevTokenValidator)


def test_factory_fails_closed_when_local_token_validation_disallowed() -> None:
    settings = Settings(allow_local_token_validation=False)

    with pytest.raises(TokenValidatorError, match="entra_authority"):
        create_token_validator(settings)


def _entra_test_material(*, audience: str) -> tuple[str, dict[str, object]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": "test-key", "use": "sig", "alg": "RS256"})
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "oid": "user-1",
            "tid": "tenant",
            "iss": "https://login.example/tenant/v2.0",
            "aud": audience,
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )
    return token, public_jwk


def _entra_mock_client(jwk: dict[str, object]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": "https://login.example/tenant/v2.0",
                    "jwks_uri": "https://login.example/keys",
                },
            )
        if request.url.path == "/keys":
            return httpx.Response(200, json={"keys": [jwk]})
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_entra_validator_rejects_authority_with_tenant_path() -> None:
    with pytest.raises(TokenValidatorError, match="without a tenant or version path"):
        EntraTokenValidator(
            authority="https://login.microsoftonline.com/tenant/v2.0",
            tenant_id="tenant",
            client_id="client-1",
        )


@pytest.mark.asyncio
async def test_entra_validator_verifies_real_signature_issuer_audience_and_lifetime() -> None:
    token, jwk = _entra_test_material(audience="api://client-1")
    client = _entra_mock_client(jwk)
    validator = EntraTokenValidator(
        authority="https://login.example",
        tenant_id="tenant",
        client_id="client-1",
        http_client=client,
    )

    claims = await validator.validate(token, method="GET", path="/sessions")

    assert claims["oid"] == "user-1"
    await client.aclose()


@pytest.mark.asyncio
async def test_entra_validator_rejects_wrong_audience() -> None:
    token, jwk = _entra_test_material(audience="api://another-client")
    client = _entra_mock_client(jwk)
    validator = EntraTokenValidator(
        authority="https://login.example",
        tenant_id="tenant",
        client_id="client-1",
        http_client=client,
    )

    with pytest.raises(AuthenticationError, match="Invalid Microsoft Entra"):
        await validator.validate(token, method="GET", path="/sessions")
    await client.aclose()


@pytest.mark.asyncio
async def test_entra_validator_rejects_wrong_tenant_claim() -> None:
    token, jwk = _entra_test_material(audience="api://client-1")
    client = _entra_mock_client(jwk)
    validator = EntraTokenValidator(
        authority="https://login.example",
        tenant_id="different-tenant",
        client_id="client-1",
        http_client=client,
    )
    validator._issuer = "https://login.example/tenant/v2.0"
    validator._keys_by_id = {"test-key": jwt.PyJWK.from_dict(jwk)}

    with pytest.raises(AuthenticationError, match="unexpected tenant"):
        await validator.validate(token, method="GET", path="/sessions")
    await client.aclose()


def test_factory_prefers_real_entra_validation_over_local_opt_in() -> None:
    validator = create_token_validator(
        Settings(
            entra_authority="https://login.example",
            entra_tenant_id="tenant",
            entra_client_id="client-1",
            allow_local_token_validation=True,
        )
    )

    assert isinstance(validator, EntraTokenValidator)
