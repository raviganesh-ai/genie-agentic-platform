from __future__ import annotations

import pytest

from app.config.settings import Settings
from app.security.token_validator import (
    AuthenticationError,
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

    with pytest.raises(TokenValidatorError, match="allow_local_token_validation"):
        create_token_validator(settings)
